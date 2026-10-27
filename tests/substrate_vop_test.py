"""Exercise real Houdini VOP defaults, sockets and USD cook dependencies."""
import sys
from pathlib import Path
import unittest
import tempfile
import json
import hou
from pxr import UsdShade,Usd
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'integrations/houdini/python3.13libs')]
import hdue_substrate
from substrate_graph import read,signature


class VopTests(unittest.TestCase):
    def tearDown(self):
        for name in ('/mat','/stage'):
            for node in hou.node(name).children(): node.destroy()

    def test_defaults_and_surface_socket(self):
        network=hdue_substrate.create()
        slab=network.node('slab')
        self.assertEqual(slab.outputDataTypes(),('bsdf',))
        self.assertAlmostEqual(slab.evalParm('roughness'),.5)
        self.assertEqual(tuple(slab.evalParmTuple('diffuse_albedo')),(.18,.18,.18))

    def test_live_parameters_and_structural_restore(self):
        network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
        self.assertAlmostEqual(network.node('layer').evalParm('thickness'),.01)
        source=hou.node('/stage').createNode('sublayer')
        source.parm('filepath1').set(str(ROOT/'samples/substrate/showcase.usda'))
        target=hdue_substrate.author_lop(network,'/Looks/WaterCoating',source)
        def current(): return read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/WaterCoating')),Usd.TimeCode.Default())
        original=signature(current())
        network.node('amount').parm('value').set(.35)
        self.assertAlmostEqual(current()['nodes']['amount']['properties']['value'],.35,places=6)
        self.assertEqual(signature(current()),original)
        network.node('layer').parm('thickness').set(.2)
        self.assertEqual(signature(current()),original)
        network.node('layer').parm('thickness').set(.01)
        self.assertEqual(signature(current()),original)
        network.node('layer').parm('parameter_blending').set(1)
        self.assertNotEqual(signature(current()),original)
        network.node('layer').parm('parameter_blending').set(0)
        self.assertEqual(signature(current()),original)

    def test_texture_sockets_and_named_outputs(self):
        network=hdue_substrate.create(); slab=network.node('slab')
        uv=network.createNode(hdue_substrate.type_name('uv'),'uv')
        texture=network.createNode(hdue_substrate.type_name('texture'),'image')
        texture.parm('file').set(str(ROOT/'samples/materialx/textures/warm.png'))
        texture.parm('color_space').set('raw'); texture.setInput(0,uv)
        channel=network.createNode(hdue_substrate.type_name('channel'),'roughness')
        channel.setInput(0,texture,1); channel.parm('channel').set('a')
        normal=network.createNode(hdue_substrate.type_name('normalmap'),'normal')
        normal.setInput(0,texture)
        slab.setInput(0,texture); slab.setInput(2,channel); slab.setInput(3,normal)
        result=hdue_substrate.graph(network)
        self.assertEqual(len(texture.outputDataTypes()),6)
        self.assertEqual(uv.outputDataTypes(),('vector2',))
        self.assertEqual(result['nodes']['roughness']['inputs']['value'],dict(node='image',output='rgba'))
        self.assertEqual(result['nodes']['uv']['inputs']['scale'],[1.,1.])
        source=hou.node('/stage').createNode('sublayer')
        source.parm('filepath1').set(str(ROOT/'samples/substrate/showcase.usda'))
        target=hdue_substrate.author_lop(network,'/Looks/Textured',source)
        exported=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
        self.assertEqual(signature(result),signature(exported))
        texture.parm('address').set('clamp')
        self.assertNotEqual(signature(result),signature(hdue_substrate.graph(network)))

    def test_burst_edits_use_one_native_cook_and_rewires_still_update(self):
        network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
        source=hou.node('/stage').createNode('sublayer')
        source.parm('filepath1').set(str(ROOT/'samples/substrate/showcase.usda'))
        target=hdue_substrate.author_lop(network,'/Looks/WaterCoating',source)
        def current(): return read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/WaterCoating')),Usd.TimeCode.Default())
        current(); initial=target.cookCount()
        for i in range(100): network.node('amount').parm('value').set(i/100)
        self.assertEqual(target.cookCount(),initial,'An edit callback forced an immediate cook')
        self.assertAlmostEqual(current()['nodes']['amount']['properties']['value'],.99,places=6)
        self.assertEqual(target.cookCount(),initial+1)
        alternate=network.createNode(hdue_substrate.type_name('scalar'),'alternate')
        alternate.parm('parameter_name').set('Other'); alternate.parm('value').set(.4)
        network.node('clamp_amount').setInput(0,alternate)
        self.assertAlmostEqual(current()['nodes']['alternate']['properties']['value'],.4,places=6)
        self.assertNotIn('amount',current()['nodes'])

    def test_incomplete_connections_keep_a_stage_and_recover(self):
        from substrate_graph import ERROR_ATTRIBUTE
        network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
        source=hou.node('/stage').createNode('sublayer')
        source.parm('filepath1').set(str(ROOT/'samples/substrate/showcase.usda'))
        target=hdue_substrate.author_lop(network,'/Looks/WaterCoating',source)
        target.stage(); original=signature(hdue_substrate.graph(network))
        for consumer,index,upstream in [(network.node('layer'),0,network.node('weight')),
                (next(n for n in network.children() if n.type().name()=='subnetconnector'),0,network.node('layer'))]:
            consumer.setInput(index,None)
            stage=target.stage(); self.assertTrue(stage); self.assertFalse(target.errors())
            self.assertTrue(stage.GetPrimAtPath('/Looks/WaterCoating').GetAttribute(ERROR_ATTRIBUTE).Get())
            with self.assertRaises(ValueError): read(UsdShade.Material(stage.GetPrimAtPath('/Looks/WaterCoating')),Usd.TimeCode.Default())
            consumer.setInput(index,upstream)
            recovered=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/WaterCoating')),Usd.TimeCode.Default())
            self.assertEqual(signature(recovered),original)
            network.node('amount').parm('value').set(.3)
            self.assertAlmostEqual(read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/WaterCoating')),Usd.TimeCode.Default())['nodes']['amount']['properties']['value'],.3,places=6)

    def test_deep_optional_disconnect_and_legacy_author_upgrade(self):
        network=hdue_substrate.create(); slab=network.node('slab')
        normal=network.createNode(hdue_substrate.type_name('normalmap'),'normal')
        slab.setInput(3,normal)
        source=hou.node('/stage').createNode('sublayer')
        source.parm('filepath1').set(str(ROOT/'samples/substrate/showcase.usda'))
        target=hdue_substrate.author_lop(network,'/Looks/Test',source)
        # Simulate a saved author LOP from before structural revision tracking.
        target.removeSpareParmTuple(target.parmTuple(hdue_substrate.REVISION_PARM))
        target.stage(); self.assertTrue(target.parm(hdue_substrate.REVISION_PARM))
        count=target.cookCount(); slab.setInput(3,None)
        self.assertEqual(target.cookCount(),count,'Wiring notification forced a synchronous cook')
        disconnected=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/Test')),Usd.TimeCode.Default())
        self.assertNotIn('normal',disconnected['nodes'])
        slab.setInput(3,normal)
        recovered=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/Test')),Usd.TimeCode.Default())
        self.assertIn('normal',recovered['nodes'])

    def test_disconnect_overrides_the_original_usd_material(self):
        from generate_substrate_textures import vops
        file=ROOT/'samples/substrate/textures.usda'; stage=Usd.Stage.Open(str(file))
        original=read(UsdShade.Material(stage.GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
        network=vops(hou.node('/mat'),original)
        source=hou.node('/stage').createNode('sublayer'); source.parm('filepath1').set(str(file))
        target=hdue_substrate.author_lop(network,'/Looks/Textured',source)
        target.stage(); base=network.node('base')
        for index in (2,3): base.setInput(index,None)
        result=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
        self.assertEqual(signature(result),signature(hdue_substrate.graph(network)))
        self.assertNotIn('normal',result['nodes']); self.assertNotIn('roughness',result['nodes'])
        self.assertAlmostEqual(result['nodes']['base']['inputs']['roughness'],.5)
        base.setInput(2,network.node('roughness')); base.setInput(3,network.node('normal'))
        restored=read(UsdShade.Material(target.stage().GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
        self.assertEqual(signature(restored),signature(original))

    def test_preparation_errors_are_published_once_until_recovery(self):
        from hdue_materialx import PreparedMaterials
        from substrate_graph import author
        stage=Usd.Stage.CreateInMemory()
        graph=dict(version=1,output='surface',nodes={'surface':dict(type='slab')})
        material=author(stage,'/Looks/Test',graph)
        graph=read(material,Usd.TimeCode.Default())
        class Api:
            def __init__(self): self.errors=[]; self.successes=0
            def surfaces(self,session,*args,**kwargs):
                if kwargs.get('error'): self.errors.append((session,kwargs['error']))
                else: self.successes+=1
                return True
            def materials(self,*args): return True
        api=Api(); resolver=PreparedMaterials()
        for _ in range(100):
            with self.assertRaisesRegex(ValueError,'unprepared'):
                resolver.sync(stage,Usd.TimeCode.Default(),None,1,api)
        self.assertEqual(len(api.errors),1)
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'library.json'
            file.write_text(json.dumps(dict(version=1,compiler=2,materials=[dict(signature=signature(graph),base_identity='',
                asset='/Game/HdUE/Test.Test',preview_asset='/Game/HdUE/Test_Mask.Test_Mask')])))
            self.assertTrue(resolver.sync(stage,Usd.TimeCode.Default(),None,1,api,str(file)))
            self.assertEqual(api.successes,1)
            self.assertIsNone(resolver.published_error)
            self.assertFalse(resolver.sync(stage,Usd.TimeCode.Default(),None,1,api,str(file)))


if __name__=='__main__': unittest.main()
