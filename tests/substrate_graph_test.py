"""Typed authoring, USD/reference round trips and dynamic/static identity."""
import copy
import sys
from pathlib import Path
import unittest
import tempfile
from pxr import Usd,UsdShade,Sdf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from substrate_graph import validate,author,read,signature,live_parameters


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.graph=dict(version=1,output='surface',nodes={
            'roughness':dict(type='scalar',properties={'parameter_name':'Roughness','value':.08}),
            'surface':dict(type='slab',inputs={'roughness':{'node':'roughness'}})})

    def test_usd_roundtrip(self):
        stage=Usd.Stage.CreateInMemory(); material=author(stage,'/Looks/Test',self.graph)
        self.assertEqual(signature(self.graph),signature(read(material,Usd.TimeCode.Default())))

    def test_disconnect_blocks_weaker_usd_connections(self):
        base=Usd.Stage.CreateInMemory(); author(base,'/Looks/Test',self.graph)
        stage=Usd.Stage.CreateInMemory(); stage.GetRootLayer().subLayerPaths=[base.GetRootLayer().identifier]
        disconnected=dict(version=1,output='surface',nodes={'surface':dict(type='slab',inputs={'roughness':.7})})
        material=author(stage,'/Looks/Test',disconnected)
        result=read(material,Usd.TimeCode.Default())
        self.assertEqual(signature(result),signature(disconnected))
        self.assertAlmostEqual(result['nodes']['surface']['inputs']['roughness'],.7,places=6)
        self.assertNotIn('roughness',result['nodes'])
        material=author(stage,'/Looks/Test',self.graph)
        self.assertEqual(signature(read(material,Usd.TimeCode.Default())),signature(self.graph))

    def test_parameter_edit_does_not_change_compiled_identity(self):
        before=signature(self.graph); self.graph['nodes']['roughness']['properties']['value']=.9
        self.assertEqual(before,signature(self.graph))
        self.graph['nodes']['roughness']['properties']['parameter_name']='Another'
        self.assertNotEqual(before,signature(self.graph))

    def test_direct_values_uv_channel_and_normal_controls_are_live(self):
        from generate_substrate_textures import example_graph
        graph=example_graph(Path('textures')); before=signature(graph)
        original={p['key']:p for p in live_parameters(graph)}
        graph['nodes']['coat']['inputs']['roughness']=.4
        graph['nodes']['coat']['inputs']['f0']=[.1,.2,.3]
        graph['nodes']['uv']['inputs']['scale']=[4.,2.]
        graph['nodes']['uv']['inputs']['offset']=[.25,.75]
        graph['nodes']['normal'].setdefault('properties',{})['flip_green']=False
        graph['nodes']['roughness']['properties']['channel']='a'
        self.assertEqual(before,signature(graph))
        changed={p['key']:p for p in live_parameters(graph)}
        for key in ('coat.inputs.roughness','coat.inputs.f0','uv.inputs.scale','uv.inputs.offset',
                    'normal.properties.flip_green','roughness.properties.channel'):
            self.assertEqual(original[key]['name'],changed[key]['name'])
            self.assertNotEqual(original[key]['value'],changed[key]['value'])
        self.assertEqual(changed['roughness.properties.channel']['value'],[0.,0.,0.,1.])
        self.assertNotIn('base.inputs.diffuse_albedo',changed)
        graph['nodes']['base']['inputs']['diffuse_albedo']=[.1,.2,.3]
        del graph['nodes']['base_color']
        self.assertNotEqual(before,signature(graph))

    def test_wrong_types_cycles_and_collisions_rejected(self):
        for modify in (
            lambda g:g['nodes']['surface']['inputs'].update(roughness={'node':'surface'}),
            lambda g:g['nodes']['roughness']['properties'].update(value=float('nan')),
            lambda g:g['nodes']['roughness']['properties'].update(parameter_name='HdUE_reserved'),
            lambda g:g['nodes'].update(unused=dict(type='constant'))):
            graph=copy.deepcopy(self.graph); modify(graph)
            with self.assertRaises(ValueError): validate(graph)

    def test_materialx_relationship_remaps_under_reference(self):
        asset=Usd.Stage.CreateInMemory(); root=asset.DefinePrim('/Asset'); asset.SetDefaultPrim(root)
        UsdShade.Material.Define(asset,'/Asset/Base')
        graph=dict(version=1,output='base',nodes={'base':dict(type='materialx',properties={'material':'/Asset/Base'})})
        author(asset,'/Asset/Material',graph)
        stage=Usd.Stage.CreateInMemory(); prim=stage.DefinePrim('/Renamed'); prim.GetReferences().AddReference(asset.GetRootLayer().identifier)
        result=read(UsdShade.Material(stage.GetPrimAtPath('/Renamed/Material')),Usd.TimeCode.Default())
        self.assertEqual(result['nodes']['base']['properties']['material'],'/Renamed/Base')
        self.assertEqual(signature(graph),signature(result))

    def test_texture_outputs_and_reference_asset_anchoring(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'image.png').write_bytes(b'fixture')
            asset=Usd.Stage.CreateNew(str(root/'asset.usda')); asset.SetDefaultPrim(asset.DefinePrim('/Asset'))
            graph=dict(version=1,output='slab',nodes={
                'uv':dict(type='uv',inputs={'scale':[2.,3.],'offset':[.1,.2]}),
                'image':dict(type='texture',inputs={'uv':{'node':'uv'}},properties={'file':'image.png','color_space':'raw'}),
                'channel':dict(type='channel',inputs={'value':{'node':'image','output':'rgba'}},properties={'channel':'a'}),
                'normal':dict(type='normalmap',inputs={'color':{'node':'image'}}),
                'slab':dict(type='slab',inputs={'diffuse_albedo':{'node':'image'},'roughness':{'node':'channel'},'normal':{'node':'normal'}})})
            material=author(asset,'/Asset/Material',graph); asset.GetRootLayer().Save()
            direct=read(material,Usd.TimeCode.Default())
            self.assertEqual(direct['nodes']['image']['properties']['file'],str((root/'image.png').resolve()))
            stage=Usd.Stage.CreateInMemory(); stage.DefinePrim('/Referenced').GetReferences().AddReference(str(root/'asset.usda'))
            composed=read(UsdShade.Material(stage.GetPrimAtPath('/Referenced/Material')),Usd.TimeCode.Default())
            self.assertEqual(signature(direct),signature(composed))
            self.assertEqual(composed['nodes']['channel']['inputs']['value']['output'],'rgba')

    def test_texture_configuration_and_outputs_rejected(self):
        graph=dict(version=1,output='slab',nodes={
            'image':dict(type='texture',properties={'file':'image.png'}),
            'slab':dict(type='slab',inputs={'diffuse_albedo':{'node':'image'}})})
        for change in (
            lambda g:g['nodes']['image']['properties'].update(color_space='acescg'),
            lambda g:g['nodes']['image']['properties'].update(file=''),
            lambda g:g['nodes']['slab']['inputs'].update(diffuse_albedo={'node':'image','output':'a'}),
            lambda g:g['nodes']['slab']['inputs'].update(diffuse_albedo={'node':'image','output':'absent'})):
            invalid=copy.deepcopy(graph); change(invalid)
            with self.assertRaises(ValueError): validate(invalid)
        with self.assertRaisesRegex(ValueError,'UV0'):
            validate(dict(version=1,output='slab',nodes={
                'image':dict(type='texture',properties={'file':'image.png'},inputs={'uv':{'node':'uv'}}),
                'slab':graph['nodes']['slab'],'uv':dict(type='uv',properties={'index':1})}))

    def test_texture_snapshot_and_stale_content(self):
        from substrate_textures import prepare,validate_dependencies
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); image=root/'image.png'; image.write_bytes(b'one')
            graph=dict(version=1,output='slab',nodes={
                'image':dict(type='texture',properties={'file':str(image.resolve())}),
                'slab':dict(type='slab',inputs={'diffuse_albedo':{'node':'image'}})})
            textures,deps,sets=prepare(graph,root/'prepared','/Game/HdUE/Test')
            self.assertEqual(Path(textures['image']['source']).read_bytes(),b'one')
            data=dict(dependencies=deps,texture_sets=sets); validate_dependencies(data)
            image.write_bytes(b'two')
            with self.assertRaisesRegex(ValueError,'texture changed'): validate_dependencies(data)

    def test_udim_membership_change_is_rejected(self):
        from substrate_textures import prepare,validate_dependencies
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for tile in (1001,1002): (root/f'image.{tile}.png').write_bytes(str(tile).encode())
            graph=dict(version=1,output='slab',nodes={
                'image':dict(type='texture',properties={'file':str(root/'image.<UDIM>.png')}),
                'slab':dict(type='slab',inputs={'diffuse_albedo':{'node':'image'}})})
            textures,deps,sets=prepare(graph,root/'prepared','/Game/HdUE/Test')
            self.assertEqual(textures['image']['tiles'],[1001,1002])
            (root/'image.1003.png').write_bytes(b'new')
            with self.assertRaisesRegex(ValueError,'membership changed'): validate_dependencies(dict(dependencies=deps,texture_sets=sets))


if __name__=='__main__': unittest.main()
