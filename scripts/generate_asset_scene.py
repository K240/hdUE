"""Create a review USD using the actual cataloged project assets."""
import argparse
import json
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom

def main():
    p=argparse.ArgumentParser(); p.add_argument('catalog',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--level'); p.add_argument('--mesh'); p.add_argument('--blueprint')
    p.add_argument('--mesh-only','--static-barrel',dest='mesh_only',action='store_true'); args=p.parse_args()
    data=json.loads(args.catalog.read_text(encoding='utf-8-sig'))['assets']
    def choose(explicit,kind):
        if explicit:
            if explicit not in data: raise ValueError('Asset is absent from the supplied catalog: '+explicit)
            return explicit
        return next((path for path,entry in data.items() if entry.get('kind')==kind and entry.get('bounds_cm')),None)
    level=choose(args.level,'level')
    mesh=choose(args.mesh,'static_mesh')
    bp=choose(args.blueprint,'blueprint')
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,.01); UsdGeom.SetStageUpAxis(stage,'Z')
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/World').GetPrim())
    def camera(name,b):
        lo=Gf.Vec3d(b['min'][0],-b['max'][1],b['min'][2]); hi=Gf.Vec3d(b['max'][0],-b['min'][1],b['max'][2])
        center=(lo+hi)*.5; size=max(hi-lo)
        eye=center+Gf.Vec3d(1.4,-1.8,1.0)*size
        cam=UsdGeom.Camera.Define(stage,'/Cameras/'+name)
        cam.CreateHorizontalApertureAttr(36); cam.CreateVerticalApertureAttr(27); cam.CreateFocalLengthAttr(45)
        cam.CreateClippingRangeAttr(Gf.Vec2f(max(.1,size*.0001),1000000))
        cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,center,Gf.Vec3d(0,0,1)).GetInverse())
    entries=(('Mesh',mesh),) if args.mesh_only else (('Level',level),('Mesh',mesh),('Blueprint',bp))
    for name,asset in entries:
        if not asset: continue
        x=UsdGeom.Xform.Define(stage,'/World/'+name)
        x.GetPrim().CreateAttribute('hdue:asset',Sdf.ValueTypeNames.String).Set(asset)
        if name!='Level' and not args.mesh_only and level: x.CreateVisibilityAttr('invisible')
        camera(name,data[asset]['bounds_cm'])
    if not any(asset for _,asset in entries): raise ValueError('No placeable assets with bounds in the supplied catalog')
    stage.GetRootLayer().Export(str(args.output.resolve()))
    print(args.output.resolve())

if __name__=='__main__': main()
