"""Generate a controlled Substrate mask comparison with an uncoated base.

Run using the configured Houdini Python executable through uv. Optional HIP
output places the editable Builder inside Material Library.
"""
import argparse
import os
from pathlib import Path
import sys
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'integrations/houdini/python3.13libs')]
import hdue_substrate
from substrate_graph import author


def generate(output,hip=None,runtime_config=None,single=False):
    output=output.resolve(); output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists(): raise FileExistsError('Choose a fresh comparison stage: '+str(output))
    stage=Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageUpAxis(stage,'Z'); UsdGeom.SetStageMetersPerUnit(stage,.01)
    world=UsdGeom.Xform.Define(stage,'/World'); stage.SetDefaultPrim(world.GetPrim())
    base=UsdShade.Material.Define(stage,'/Looks/SS_Coat')
    shader=UsdShade.Shader.Define(stage,'/Looks/SS_Coat/MaterialX')
    source=os.path.relpath(ROOT/'samples/materialx/standard_surface.mtlx',output.parent).replace('\\','/')
    shader.SetSourceAsset(Sdf.AssetPath(source),'mtlx'); shader.SetSourceAssetSubIdentifier('SS_Coat','mtlx')
    shader.CreateInput('coat',Sdf.ValueTypeNames.Float).Set(0.)
    shader.CreateInput('specular_roughness',Sdf.ValueTypeNames.Float).Set(.65)
    shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
    base.CreateSurfaceOutput('mtlx').ConnectToSource(shader.ConnectableAPI(),'surface')
    preview=UsdShade.Shader.Define(stage,'/Looks/SS_Coat/Preview'); preview.CreateIdAttr('UsdPreviewSurface')
    preview.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.4,.2,.08))
    preview.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.65)
    preview.CreateOutput('surface',Sdf.ValueTypeNames.Token); base.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(),'surface')
    network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
    material=author(stage,'/Looks/WaterCoating',hdue_substrate.graph(network))
    asset=os.path.relpath(ROOT/'samples/wet_surface/asset.usda',output.parent).replace('\\','/')
    paths=[]
    names=['Masked'] if single else ['Dry_0','Coated_1','Masked']
    for i,name in enumerate(names):
        placement=UsdGeom.Xform.Define(stage,'/World/'+name)
        placement.GetPrim().GetReferences().AddReference(asset)
        placement.AddTranslateOp().Set(Gf.Vec3d(0 if single else (i-1)*135,0,60))
        mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/World/'+name+'/Sphere'))
        pv=UsdGeom.PrimvarsAPI(mesh).GetPrimvar('wetness')
        if name=='Masked':
            pv.SetInterpolation('vertex'); pv.Set([min(1.,max(0.,float(p[0])/20.+.5)) for p in mesh.GetPointsAttr().Get()])
        else:
            pv.SetInterpolation('constant'); pv.Set([0. if name=='Dry_0' else 1.])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        paths.append(str(mesh.GetPath()))
    camera=UsdGeom.Camera.Define(stage,'/Camera')
    eye=Gf.Vec3d(70,-260,140) if single else Gf.Vec3d(0,-620,175)
    target=Gf.Vec3d(0,0,60)
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(20.25); camera.CreateFocalLengthAttr(40 if single else 50)
    camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000))
    for name,eye,intensity,size in [('Key',Gf.Vec3d(-100,-250,350),12,(120,40)),('Fill',Gf.Vec3d(250,-100,180),1,(140,200))]:
        light=UsdLux.RectLight.Define(stage,'/Lights/'+name)
        light.CreateIntensityAttr(intensity); light.CreateWidthAttr(size[0]); light.CreateHeightAttr(size[1])
        light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    stage.GetRootLayer().Save()
    if hip:
        if not runtime_config: raise ValueError('HIP creation requires --runtime-config')
        os.environ['HDUE_RUNTIME_CONFIG']=str(runtime_config.resolve())
        import hou,hdue_houdini
        hip=hip.resolve(); hip.parent.mkdir(parents=True,exist_ok=True)
        if hip.exists(): raise FileExistsError(hip)
        hou.hipFile.setName(str(hip)); lopnet=hou.node('/stage')
        source=lopnet.createNode('sublayer','Comparison_scene')
        source.parm('filepath1').set('$HIP/'+os.path.relpath(output,hip.parent).replace('\\','/'))
        controls=hdue_houdini.settings_node(source,dict(metersperunit=.01,upaxis='Z'))
        library=lopnet.createNode('materiallibrary','MaterialLibrary'); library.setInput(0,controls)
        # Move the already validated editable network into Material Library.
        network=hou.moveNodesTo((network,),library)[0]
        author_node=hdue_substrate.author_lop(network,'/Looks/WaterCoating',library,paths)
        author_node.setName('Author_Substrate_USD',unique_name=True)
        note=lopnet.createStickyNote('Comparison_guide')
        arrangement='One sphere, one assigned material, with a split vertex mask.' if single else 'Same composite material on every sphere.\nLeft: mask 0. Center: mask 1. Right: split mask.'
        note.setText('CONTROLLED SUBSTRATE COMPARISON\n\n'+arrangement+'\nBase coat = 0, base roughness = 0.65.\nWater roughness = 0.08.\n\nCoverage and WaterRoughness update live.\nUse the Primvar mask preview to inspect coverage.')
        lopnet.layoutChildren(); network.layoutChildren()
        lopnet.setUserData('hdue:startupBuilder',network.path())
        author_node.setDisplayFlag(True); author_node.setCurrent(True,clear_all_selected=True)
        assert not author_node.errors() and author_node.stage()
        hou.hipFile.save(str(hip))
    return stage


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--hip',type=Path); parser.add_argument('--runtime-config',type=Path); parser.add_argument('--single',action='store_true')
    args=parser.parse_args(); generate(args.output,args.hip,args.runtime_config,args.single)
