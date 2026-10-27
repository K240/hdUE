# /// script
# requires-python = ">=3.11"
# ///
"""Create owned wet/dry/masked fixtures with a shared MaterialX binding."""
import argparse
import math
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdLux, Vt


def generate(folder):
    folder.mkdir(parents=True, exist_ok=True)
    asset=Usd.Stage.CreateNew(str((folder/'asset.usda').resolve()))
    root=UsdGeom.Xform.Define(asset,'/Asset'); asset.SetDefaultPrim(root.GetPrim())
    mesh=UsdGeom.Mesh.Define(asset,'/Asset/Sphere')
    points=[]; normals=[]; uv=[]; indices=[]; masks=[]
    rings,segments=24,48
    for r in range(rings+1):
        phi=math.pi*r/rings
        for s in range(segments+1):
            theta=math.tau*s/segments
            normal=Gf.Vec3f(math.sin(phi)*math.cos(theta),math.sin(phi)*math.sin(theta),math.cos(phi))
            points.append(normal*50); normals.append(normal); uv.append(Gf.Vec2f(s/segments,r/rings)); masks.append((normal[0]+1)*.5)
    for r in range(rings):
        for s in range(segments):
            a=r*(segments+1)+s; b=a+segments+1
            if r<rings-1: indices.extend((a,b,b+1))
            if r>0: indices.extend((a,b+1,a+1))
    mesh.CreatePointsAttr(points); mesh.CreateFaceVertexCountsAttr([3]*(len(indices)//3)); mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateNormalsAttr(normals); mesh.SetNormalsInterpolation('vertex'); mesh.CreateSubdivisionSchemeAttr('none')
    api=UsdGeom.PrimvarsAPI(mesh)
    api.CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set(uv)
    api.CreatePrimvar('wetness',Sdf.ValueTypeNames.FloatArray,'vertex').Set(masks)
    asset.GetRootLayer().Save()

    stage=Usd.Stage.CreateNew(str((folder/'showcase.usda').resolve()))
    UsdGeom.SetStageMetersPerUnit(stage,.01); UsdGeom.SetStageUpAxis(stage,'Z')
    world=UsdGeom.Xform.Define(stage,'/World'); stage.SetDefaultPrim(world.GetPrim())
    material=UsdShade.Material.Define(stage,'/Looks/SS_Coat')
    preview=UsdShade.Shader.Define(stage,'/Looks/SS_Coat/Preview')
    preview.CreateIdAttr('UsdPreviewSurface')
    preview.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.4,.2,.08))
    preview.CreateOutput('surface',Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(),'surface')
    shader=UsdShade.Shader.Define(stage,'/Looks/SS_Coat/MaterialX')
    shader.SetSourceAsset(Sdf.AssetPath('../materialx/standard_surface.mtlx'),'mtlx')
    shader.SetSourceAssetSubIdentifier('SS_Coat','mtlx'); shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput('mtlx').ConnectToSource(shader.ConnectableAPI(),'surface')
    for i,name in enumerate(('Dry','Wet','Ramp')):
        placement=UsdGeom.Xform.Define(stage,'/World/'+name)
        placement.GetPrim().GetReferences().AddReference('asset.usda')
        placement.AddTranslateOp().Set(Gf.Vec3d((i-1)*135,0,60))
        target=stage.GetPrimAtPath('/World/'+name+'/Sphere')
        UsdShade.MaterialBindingAPI.Apply(target).Bind(material)
        placement.GetPrim().CreateAttribute('hdue:surface:wet:enabled',Sdf.ValueTypeNames.Bool).Set(i>0)
        if i==2:
            placement.GetPrim().CreateAttribute('hdue:surface:wet:maskPrimvar',Sdf.ValueTypeNames.Token).Set('wetness')
            subset=UsdGeom.Subset.CreateGeomSubset(UsdGeom.Imageable(target),'Top','face',list(range(240)),'materialBind','nonOverlapping')
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)
            subset.GetPrim().CreateAttribute('hdue:surface:wet:roughness',Sdf.ValueTypeNames.Float).Set(.2)
    camera=UsdGeom.Camera.Define(stage,'/Camera'); eye=Gf.Vec3d(230,-600,270); target=Gf.Vec3d(0,0,50)
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(20.25); camera.CreateFocalLengthAttr(40)
    camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000))
    for name,eye,intensity in [('Key',Gf.Vec3d(-200,-250,450),8),('Fill',Gf.Vec3d(250,100,250),4)]:
        light=UsdLux.RectLight.Define(stage,'/Lights/'+name)
        light.CreateIntensityAttr(intensity); light.CreateWidthAttr(200); light.CreateHeightAttr(150)
        light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    stage.GetRootLayer().Save()


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'samples/wet_surface')
    generate(parser.parse_args().output)
