"""Run with Houdini's Python through uv to generate the editable VOP example."""
import argparse
from pathlib import Path
import sys
from pxr import Usd,UsdShade,UsdGeom,Sdf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integrations/houdini/python3.13libs'))
import hdue_substrate
from substrate_graph import author


def generate(folder,hip=None):
    folder.mkdir(parents=True,exist_ok=True)
    stage=Usd.Stage.CreateNew(str((folder/'showcase.usda').resolve()))
    import os
    stage.GetRootLayer().subLayerPaths=[os.path.relpath(ROOT/'samples/wet_surface/showcase.usda',folder).replace('\\','/')]
    network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
    material=author(stage,'/Looks/WaterCoating',hdue_substrate.graph(network))
    for name in ('Dry','Wet','Ramp'):
        stage.GetPrimAtPath('/World/'+name).CreateAttribute('hdue:surface:wet:enabled',Sdf.ValueTypeNames.Bool).Set(False)
        if name!='Dry':
            mesh=stage.GetPrimAtPath('/World/'+name+'/Sphere')
            UsdShade.MaterialBindingAPI.Apply(mesh).Bind(material)
            if name=='Wet': UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('wetness',Sdf.ValueTypeNames.FloatArray,'constant').Set([1.])
            for child in mesh.GetChildren():
                if child.IsA(UsdGeom.Subset): UsdShade.MaterialBindingAPI.Apply(child).Bind(material)
    stage.GetRootLayer().Save()
    if hip:
        import hou
        source=hou.node('/stage').createNode('sublayer','source')
        source.parm('filepath1').set(str((folder/'showcase.usda').resolve()))
        hdue_substrate.author_lop(network,'/Looks/WaterCoating',source)
        hou.hipFile.save(str(hip.resolve()))
    return network


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=ROOT/'samples/substrate'); parser.add_argument('--hip',type=Path)
    args=parser.parse_args(); generate(args.output,args.hip)
