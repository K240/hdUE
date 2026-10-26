# /// script
# requires-python = ">=3.11,<3.12"
# ///
"""Owned inline USD fixture: 10 surfaces, nested graphs and interface values."""
import argparse
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdShade


def generate(source, output):
    if output.exists():
        raise FileExistsError(f"Choose a fresh USD path: {output}")
    stage=Usd.Stage.Open(str(source.resolve()))
    stage.GetRootLayer().Export(str(output.resolve()))
    stage=Usd.Stage.Open(str(output.resolve()))
    for prim in [p for p in stage.Traverse() if p.IsA(UsdShade.Material)]:
        if not prim.GetName().startswith(("SS_","OP_")):
            continue
        material=UsdShade.Material(prim)
        name=prim.GetName()
        shader=UsdShade.Shader(stage.GetPrimAtPath(str(prim.GetPath())+"/MaterialX"))
        for prop in ("info:mtlx:sourceAsset","info:mtlx:sourceAsset:subIdentifier"):
            shader.GetPrim().RemoveProperty(prop)
        shader.CreateIdAttr("ND_standard_surface_surfaceshader" if name.startswith("SS_") else "ND_open_pbr_surface_surfaceshader")
        shader.CreateImplementationSourceAttr().Set(UsdShade.Tokens.id)
        for field in list(shader.GetInputs()):
            if str(field.GetBaseName()).startswith("hdue:"):
                shader.GetPrim().RemoveProperty(field.GetFullName())
        if name=="OP_Metal":
            color=shader.GetInput("base_color")
            interface=material.CreateInput("tint",Sdf.ValueTypeNames.Color3f)
            interface.Set(color.Get())
            color.ClearSources(); color.GetAttr().Clear()
            color.ConnectToSource(interface)
        if name.endswith("_Coat"):
            parent=prim.GetPath()
            if name.startswith("OP_"):
                outer=UsdShade.NodeGraph.Define(stage,str(parent)+"/SurfaceGraph")
                target=UsdShade.Shader.Define(stage,str(outer.GetPath())+"/MaterialX")
                target.CreateIdAttr(shader.GetIdAttr().Get())
                for field in shader.GetInputs():
                    target.CreateInput(field.GetBaseName(),field.GetTypeName()).Set(field.Get())
                target.CreateOutput("out",Sdf.ValueTypeNames.Token)
                outer.CreateOutput("surface",Sdf.ValueTypeNames.Token).ConnectToSource(target.ConnectableAPI(),"out")
                material.GetSurfaceOutput("mtlx").ConnectToSource(outer.ConnectableAPI(),"surface")
                stage.RemovePrim(shader.GetPath())
                shader=target; parent=outer.GetPath()
            graph=UsdShade.NodeGraph.Define(stage,str(parent)+"/TextureGraph")
            image=UsdShade.Shader.Define(stage,str(graph.GetPath())+"/Image")
            image.CreateIdAttr("ND_image_color3")
            image.CreateInput("file",Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("textures/warm.png" if name.startswith("SS_") else "textures/cool.png"))
            image.GetInput("file").GetAttr().SetColorSpace("srgb_texture")
            image.CreateOutput("out",Sdf.ValueTypeNames.Color3f)
            uv=UsdShade.Shader.Define(stage,str(graph.GetPath())+"/UV")
            uv.CreateIdAttr("ND_texcoord_vector2")
            uv.CreateInput("index",Sdf.ValueTypeNames.Int).Set(0)
            uv.CreateOutput("out",Sdf.ValueTypeNames.Float2)
            image.CreateInput("texcoord",Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),"out")
            multiply=UsdShade.Shader.Define(stage,str(graph.GetPath())+"/Tint")
            multiply.CreateIdAttr("ND_multiply_color3")
            multiply.CreateInput("in1",Sdf.ValueTypeNames.Color3f).ConnectToSource(image.ConnectableAPI(),"out")
            interface=material.CreateInput("textureTint",Sdf.ValueTypeNames.Color3f)
            interface.Set(Gf.Vec3f(0.9,0.8,0.7))
            graph.CreateInput("tint",Sdf.ValueTypeNames.Color3f).ConnectToSource(interface)
            multiply.CreateInput("in2",Sdf.ValueTypeNames.Color3f).ConnectToSource(graph.GetInput("tint"))
            multiply.CreateOutput("out",Sdf.ValueTypeNames.Color3f)
            graph.CreateOutput("color",Sdf.ValueTypeNames.Color3f).ConnectToSource(multiply.ConnectableAPI(),"out")
            shader.CreateInput("base_color",Sdf.ValueTypeNames.Color3f).ConnectToSource(graph.ConnectableAPI(),"color")
    stage.GetRootLayer().Save()
    print(output)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,default=Path(__file__).resolve().parent.parent/"samples/materialx/showcase.usda")
    p.add_argument("--output",type=Path,default=Path(__file__).resolve().parent.parent/"samples/materialx/inline.usda")
    args=p.parse_args(); generate(args.source,args.output)
