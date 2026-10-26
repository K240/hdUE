"""Run with uv run -- <HFS>/bin/hython.exe tests/houdini_cook.py."""
import os
from pathlib import Path
import sys
import tempfile
import json
import hou
from pxr import Gf,Sdf,Usd,UsdGeom

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'integrations/houdini/python3.13libs'))
_runtime_directory=tempfile.TemporaryDirectory(prefix='hdue-cook-test-')
_runtime_config=Path(_runtime_directory.name)/'runtime.json'
_runtime_config.write_text(json.dumps({'version':1}),encoding='utf-8')
os.environ['HDUE_RUNTIME_CONFIG']=str(_runtime_config)
import hdue_houdini as integration

source=hou.node('/stage').createNode('pythonscript','test_source')
source.parm('python').set('''from pxr import UsdGeom, Sdf
stage=hou.pwd().editableStage()
prim=UsdGeom.Xform.Define(stage,'/Native').GetPrim()
prim.CreateAttribute('hdue:asset',Sdf.ValueTypeNames.String).Set('/Game/HdUE/SM_Nanite.SM_Nanite')
parent=UsdGeom.Xform.Define(stage,'/Parent')
parent.AddTranslateOp().Set((1,2,3)); parent.AddScaleOp().Set((2,2,2))
mesh=UsdGeom.Mesh.Define(stage,'/Parent/MeshAsset')
mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)])
mesh.CreateFaceVertexCountsAttr([3]); mesh.CreateFaceVertexIndicesAttr([0,1,2])
mesh.GetPrim().CreateAttribute('hdue:asset',Sdf.ValueTypeNames.String).Set('/Game/HdUE/SM_Nanite.SM_Nanite')
''')
before=source.stage().GetRootLayer().ExportToString()
node=integration.settings_node(source,{'metersperunit':.01,'upaxis':'y'})
stage=node.stage()
assert not node.errors(),node.errors()
assert UsdGeom.GetStageMetersPerUnit(stage)==1.
assert UsdGeom.GetStageUpAxis(stage)=='Y'
proxy=UsdGeom.Mesh(stage.GetPrimAtPath('/Native/__HdUEAssetProxy'))
assert proxy and len(proxy.GetPointsAttr().Get())==8
assert UsdGeom.PrimvarsAPI(proxy).GetPrimvar('hdue:asset').Get()=='/Game/HdUE/SM_Nanite.SM_Nanite'
assert abs(UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(proxy.GetPrim()).ComputeAlignedRange().GetSize()[0]-1.)<1e-5
assert stage.GetMetadata('renderSettingsPrimPath')=='/Render/hdUE',stage.GetRootLayer().ExportToString()
settings=stage.GetPrimAtPath('/Render/hdUE')
assert settings.GetAttribute('hdUE:renderMode').Get()=='realtime'
node.parm('hdue_mode').set('pathtraced')
assert node.stage().GetPrimAtPath('/Render/hdUE').GetAttribute('hdUE:renderMode').Get()=='pathtraced'
assert source.stage().GetRootLayer().ExportToString()==before
native=integration.settings_node(source) # native Solaris input: Y-up / meters
native_stage=native.stage()
assert not native.errors(),native.errors()
mesh=native_stage.GetPrimAtPath('/Parent/MeshAsset')
assert UsdGeom.Imageable(mesh).GetPurposeAttr().Get()=='guide'
mesh_proxy=native_stage.GetPrimAtPath('/Parent/MeshAsset/__HdUEAssetProxy')
matrix=UsdGeom.Xformable(mesh_proxy).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
assert Gf.IsClose(matrix.ExtractTranslation(),Gf.Vec3d(1,2,3),1e-9)
assert abs(matrix.TransformDir(Gf.Vec3d(1,0,0)).GetLength()-2)<1e-9
again=integration.settings_node(native)
assert again.stage().GetPrimAtPath('/Parent/MeshAsset/__HdUEAssetProxy')
assert not again.errors(),again.errors()
clear=source.parent().createNode('pythonscript','clear_asset_reference'); clear.setInput(0,native)
clear.parm('python').set('stage=hou.pwd().editableStage()\nstage.GetPrimAtPath("/Parent/MeshAsset").GetAttribute("hdue:asset").Set("")')
removed=integration.settings_node(clear)
assert not removed.stage().GetPrimAtPath('/Parent/MeshAsset/__HdUEAssetProxy').IsActive()
assert UsdGeom.Imageable(removed.stage().GetPrimAtPath('/Parent/MeshAsset')).GetPurposeAttr().Get()=='default'
assert not removed.errors(),removed.errors()
restore=source.parent().createNode('pythonscript','restore_asset_reference'); restore.setInput(0,removed)
restore.parm('python').set('stage=hou.pwd().editableStage()\nstage.GetPrimAtPath("/Parent/MeshAsset").GetAttribute("hdue:asset").Set("/Game/HdUE/SM_Nanite.SM_Nanite")')
restored=integration.settings_node(restore)
assert restored.stage().GetPrimAtPath('/Parent/MeshAsset/__HdUEAssetProxy').IsActive()
assert not restored.errors(),restored.errors()
print('PASS: real Solaris LOP cook, native proxy, render settings and source preservation')
