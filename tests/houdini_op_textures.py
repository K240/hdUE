"""Run through uv run -- <HFS>/bin/hython.exe tests/houdini_op_textures.py."""
import os
from pathlib import Path
import sys
import unittest
import tempfile
import json
import hou
import numpy as np
from pxr import Sdf, UsdShade

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integrations/houdini/python3.13libs'))
_runtime_directory=tempfile.TemporaryDirectory(prefix='hdue-cop-test-')
_runtime_config=Path(_runtime_directory.name)/'runtime.json'
_runtime_config.write_text(json.dumps({'version':1}),encoding='utf-8')
os.environ.setdefault('HDUE_RUNTIME_CONFIG',str(_runtime_config))
import hdue_houdini as integration
import hdue_op_textures as textures


def fixture(name='op_textures'):
    obj=hou.node('/obj').createNode('geo',name)
    obj.deleteItems(obj.children())
    net=obj.createNode('copnet','images')
    net.parm('setres').set(1); net.parmTuple('res').set((32,16))
    color=net.createNode('constant','color')
    color.parm('signature').set('f3'); color.parmTuple('f3').set((.8,.05,.02))
    output=net.createNode('null','OUT'); output.setInput(0,color)
    source=hou.node('/stage').createNode('pythonscript',name)
    source.parm('python').set('''from pxr import UsdShade,UsdGeom,Sdf
stage=hou.pwd().editableStage()
mesh=UsdGeom.Mesh.Define(stage,'/Quad')
mesh.CreatePointsAttr([(-2,0,-1),(2,0,-1),(2,0,1),(-2,0,1)])
mesh.CreateFaceVertexCountsAttr([4]); mesh.CreateFaceVertexIndicesAttr([0,3,2,1])
mesh.CreateSubdivisionSchemeAttr('none')
UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'faceVarying').Set([(0,0),(0,1),(1,1),(1,0)])
material=UsdShade.Material.Define(stage,'/Looks/COP')
surface=UsdShade.Shader.Define(stage,'/Looks/COP/Surface')
surface.CreateIdAttr('UsdPreviewSurface')
surface.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set((1,1,1))
image=UsdShade.Shader.Define(stage,'/Looks/COP/Image')
image.CreateIdAttr('UsdUVTexture')
image.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(%r))
image.CreateOutput('rgb',Sdf.ValueTypeNames.Float3)
surface.GetInput('diffuseColor').ConnectToSource(image.ConnectableAPI(),'rgb')
material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(),'surface')
UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
''' % ('op:'+output.path()))
    return obj,color,output,source


def materialx_source(source):
    edit=source.parent().createNode('pythonscript','mtlx_op_edit'); edit.setInput(0,source)
    edit.parm('python').set('''from pxr import UsdShade,Sdf
stage=hou.pwd().editableStage()
material=UsdShade.Material(stage.GetPrimAtPath('/Looks/COP'))
material.GetSurfaceOutput().DisconnectSource()
surface=UsdShade.Shader(stage.GetPrimAtPath('/Looks/COP/Surface'))
surface.CreateIdAttr('ND_standard_surface_surfaceshader')
surface.CreateInput('base_color',Sdf.ValueTypeNames.Color3f).ConnectToSource(UsdShade.Shader(stage.GetPrimAtPath('/Looks/COP/Image')).ConnectableAPI(),'out')
image=UsdShade.Shader(stage.GetPrimAtPath('/Looks/COP/Image'))
image.CreateIdAttr('ND_image_color3'); image.CreateOutput('out',Sdf.ValueTypeNames.Color3f)
material.CreateSurfaceOutput('mtlx').ConnectToSource(surface.ConnectableAPI(),'out')
''')
    return edit


class OpTextures(unittest.TestCase):
    def tearDown(self):
        textures.close()

    def test_live_image_cache_and_layer_preservation(self):
        obj,color,output,source=fixture()
        before=source.stage().GetRootLayer().ExportToString()
        bridge=integration.settings_node(source)
        stage=bridge.stage()
        self.assertFalse(bridge.errors(),bridge.errors())
        attr=stage.GetPrimAtPath('/Looks/COP/Image').GetAttribute('inputs:file')
        first=Path(attr.Get().path)
        self.assertTrue(first.is_file())
        self.assertEqual(hou.imageResolution(str(first)),(32,16))
        self.assertEqual(textures.cache().resolve('op:'+output.path()),str(first))
        self.assertFalse(textures.cache().dirty(bridge.node('asset_bridge')))
        color.parmTuple('f3').set((.02,.05,.8))
        self.assertTrue(textures.cache().dirty(bridge.node('asset_bridge')))
        textures.refresh(bridge.node('asset_bridge'))
        second=Path(bridge.stage().GetPrimAtPath('/Looks/COP/Image').GetAttribute('inputs:file').Get().path)
        self.assertNotEqual(first,second)
        self.assertTrue(first.is_file(),'An in-flight frame can still use the previous file')
        self.assertFalse(textures.cache().dirty(bridge.node('asset_bridge')))
        self.assertEqual(source.stage().GetRootLayer().ExportToString(),before)
        self.assertEqual(len([n for n in hou.node('/out').children() if n.name().startswith('__hdue_texture_export')]),1)
        output.destroy()
        self.assertTrue(textures.cache().dirty(bridge.node('asset_bridge')))
        with self.assertRaises(ValueError): textures.refresh(bridge.node('asset_bridge'))
        output=color.parent().createNode('null','OUT'); output.setInput(0,color)
        textures.refresh(bridge.node('asset_bridge'))
        self.assertFalse(bridge.errors(),bridge.errors())
        self.assertFalse(textures.cache().dirty(bridge.node('asset_bridge')))
        color.parm('f3r').setExpression('$F/100',hou.exprLanguage.Hscript)
        hou.setFrame(1); at_one=textures.cache().resolve('op:'+output.path())
        hou.setFrame(2)
        self.assertTrue(textures.cache().dirty(bridge.node('asset_bridge')))
        self.assertNotEqual(at_one,textures.cache().resolve('op:'+output.path()))
        textures.close()
        self.assertFalse(first.exists())
        self.assertFalse(any(n.name().startswith('__hdue_texture_export') for n in hou.node('/out').children()))

    def test_materialx_cop_basecolor_fallback(self):
        obj,color,output,source=fixture('mtlx_op')
        edit=materialx_source(source)
        bridge=integration.settings_node(edit)
        stage=bridge.stage()
        self.assertFalse(bridge.errors(),bridge.errors())
        material=UsdShade.Material(stage.GetPrimAtPath('/Looks/COP'))
        self.assertEqual(str(material.GetSurfaceOutput().GetConnectedSource()[0].GetPath()),'/Looks/COP/__HdUECopPreview')
        self.assertTrue(material.GetSurfaceOutput('mtlx').HasConnectedSource())
        self.assertTrue(stage.GetPrimAtPath('/Looks/COP/__HdUECopPreview/Image').GetAttribute('inputs:file').Get().path.endswith('.exr'))

    def test_reject_bad_sources_and_outputs(self):
        obj,color,output,source=fixture('bad_op')
        for uri in ('op:relative/node','op:/obj/missing','op:'+obj.path(),'op:'+output.path()+'[999]','op:'+output.path()+'<UDIM>'):
            with self.subTest(uri=uri),self.assertRaises(ValueError): textures.cache().resolve(uri)
        path=textures.cache().resolve('op:'+output.path()+'[0]',True)
        self.assertEqual(hou.imageResolution(path),(32,16))

    def test_legacy_cop_current_frame(self):
        network=hou.node('/img').createNode('img','legacy_op')
        color=network.createNode('color','color')
        color.parm('overridesize').set(1)
        color.parmTuple('size').set((32,16))
        color.parmTuple('color').set((.7,.1,.05,1.))
        frame=hou.frame()
        path=textures.cache().resolve('op:'+color.path(),True)
        self.assertEqual(hou.imageResolution(path),(32,16))
        self.assertEqual(hou.frame(),frame)
        color.parm('colorb').set(.9)
        self.assertNotEqual(path,textures.cache().resolve('op:'+color.path(),True))

    def test_copernicus_output_port_and_raw_values(self):
        obj,color,output,source=fixture('op_ports')
        split=color.parent().createNode('channelsplit','split'); split.setInput(0,color)
        red=textures.cache().resolve('op:'+split.path()+'[0]',True)
        blue=textures.cache().resolve('op:'+split.path()+'[2]',True)
        red_pixels=np.frombuffer(hou.loadImageDataFromFile(red),dtype=np.uint8).reshape(-1,4)
        blue_pixels=np.frombuffer(hou.loadImageDataFromFile(blue),dtype=np.uint8).reshape(-1,4)
        self.assertGreater(red_pixels[:,0].mean(),blue_pixels[:,0].mean()*5)
        self.assertLess(np.max(np.ptp(red_pixels[:,:3],axis=1)),2)


if __name__=='__main__': unittest.main()
