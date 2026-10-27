"""Generate/check numerical texture, channel, UV and UDIM render fixtures.

Use the normal Prepare-Substrate and Test-Hydra pipeline between these steps.
The check reads linear color AOVs at the centers of nine unlit mesh patches.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


def generate(folder):
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
    from substrate_graph import author
    from generate_substrate_textures import png
    folder=folder.resolve(); folder.mkdir(parents=True,exist_ok=False)
    png(folder/'value.png',lambda x,y,s:(128,64,32,192),size=4)
    png(folder/'tile.1001.png',lambda x,y,s:(255,0,0,255),size=4)
    png(folder/'tile.1002.png',lambda x,y,s:(0,255,0,255),size=4)
    stage=Usd.Stage.CreateNew(str(folder/'checks.usda')); stage.SetDefaultPrim(stage.DefinePrim('/World','Xform'))
    UsdGeom.SetStageMetersPerUnit(stage,.01); UsdGeom.SetStageUpAxis(stage,'Z')
    specs=[('srgb',[.21586,.05127,.01444]),('raw',[128/255,64/255,32/255])]
    specs += [(c,[v/255]*3) for c,v in zip('rgba',(128,64,32,192))]
    specs += [('direct_a',[192/255]*3),('udim1',[1,0,0]),('udim2',[0,1,0])]
    checks=[]
    for i,(name,expected) in enumerate(specs):
        center=((i%3-1)*60,(1-i//3)*50)
        nodes={'image':dict(type='texture',properties={'file':'value.png','color_space':'srgb' if name=='srgb' else 'raw'})}
        source=dict(node='image')
        if name in 'rgba':
            nodes['channel']=dict(type='channel',inputs={'value':dict(node='image',output='rgba')},properties={'channel':name})
            nodes['gray']=dict(type='color_from_float',inputs={'value':dict(node='channel')}); source=dict(node='gray')
        elif name=='direct_a':
            nodes['gray']=dict(type='color_from_float',inputs={'value':dict(node='image',output='a')}); source=dict(node='gray')
        elif name.startswith('udim'):
            nodes['uv']=dict(type='uv',inputs={'scale':[0.,0.],'offset':[.5 if name=='udim1' else 1.5,.5]})
            nodes['image']['inputs']={'uv':dict(node='uv')}; nodes['image']['properties']['file']='tile.<UDIM>.png'
        nodes['surface']=dict(type='unlit',inputs={'emission':source})
        material=author(stage,'/Looks/'+name,dict(version=1,output='surface',nodes=nodes))
        mesh=UsdGeom.Mesh.Define(stage,'/World/'+name); x,z=center; radius=16
        mesh.CreatePointsAttr([(x-radius,0,z-radius),(x+radius,0,z-radius),(x+radius,0,z+radius),(x-radius,0,z+radius)])
        mesh.CreateFaceVertexCountsAttr([4]); mesh.CreateFaceVertexIndicesAttr([0,1,2,3]); mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateNormalsAttr([(0,-1,0)]*4); mesh.SetNormalsInterpolation('vertex')
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set([(0,0),(1,0),(1,1),(0,1)])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        checks.append(dict(name=name,center=center,expected=expected))
    camera=UsdGeom.Camera.Define(stage,'/Camera')
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(0,-400,0),Gf.Vec3d(0,0,0),Gf.Vec3d(0,0,1)).GetInverse())
    camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(20.25); camera.CreateFocalLengthAttr(50)
    camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000)); stage.GetRootLayer().Save()
    (folder/'expected.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')


def check(folder,renders):
    checks=json.loads((folder/'expected.json').read_text()); results=[]
    metrics=json.loads((renders/'metrics.json').read_text()); assert metrics['passed']
    for frame in metrics['frames']:
        path=renders/(frame['label']+'.pfm')
        with path.open('rb') as file:
            assert file.readline().strip()==b'PF'
            width,height=map(int,file.readline().split()); scale=float(file.readline()); pixels=file.read()
        for item in checks:
            x,z=item['center']; px=round(width*(.5+x/288)); py=round(height*(.5+z/162)) # bottom-up PFM
            samples=[struct.unpack_from(('<' if scale<0 else '>')+'3f',pixels,((py+dy)*width+px+dx)*12)
                     for dx in range(-2,3) for dy in range(-2,3)]
            actual=[sum(p[c] for p in samples)/len(samples) for c in range(3)]
            error=max(abs(a-b) for a,b in zip(actual,item['expected']))
            if not all(math.isfinite(v) for v in actual) or error>.035:
                raise AssertionError(f"{frame['mode']} {item['name']}: expected {item['expected']}, got {actual}")
            results.append(dict(mode=frame['mode'],name=item['name'],actual=actual,error=error))
    result=dict(passed=True,checks=results)
    (renders/'texture-checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'Passed {len(results)} numerical texture checks')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('mode',choices=('generate','check')); parser.add_argument('folder',type=Path)
    parser.add_argument('--renders',type=Path); args=parser.parse_args()
    if args.mode=='generate': generate(args.folder)
    else: check(args.folder,args.renders)
