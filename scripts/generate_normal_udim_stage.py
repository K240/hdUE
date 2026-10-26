# /// script
# requires-python = ">=3.11,<3.12"
# ///
"""Owned data fixtures: directional normals and asymmetric 2x2 UDIMs."""
import argparse
import math
from pathlib import Path
import struct
import zlib
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade,Vt


def png(path,size,color,marker=False):
    width,height=size
    rows=[]
    for y in range(height):
        rows.append(b"\0"+bytes(v for x in range(width) for v in ((245,245,245) if marker and x<width//4 and y<height//4 else color)))
    def chunk(tag,data):
        return struct.pack(">I",len(data))+tag+data+struct.pack(">I",zlib.crc32(tag+data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(b"".join(rows)))+chunk(b"IEND",b""))


def generate(folder):
    if folder.exists():
        raise FileExistsError("Choose a fresh normal/UDIM fixture directory")
    folder.mkdir(parents=True)
    for index,color,size in ((1001,(210,25,25),(256,256)),(1002,(25,210,25),(128,128)),(1011,(25,25,210),(256,256)),(1012,(210,190,25),(128,128))):
        png(folder/f"color.{index}.png",size,color,True)
    for index,color in ((1001,(25,25,210)),(1002,(210,25,25)),(1011,(25,210,25)),(1012,(210,25,190))):
        png(folder/f"color_swap.{index}.png",(128,128),color,True)
    for index,color in ((1001,(128,128,255)),(1002,(204,128,230)),(1011,(128,204,230)),(1012,(76,179,233))):
        png(folder/f"normal_tiles.{index}.png",(128,128),color)
    png(folder/"slope.png",(128,128),(128,204,230))
    png(folder/"flat.png",(128,128),(128,128,255))
    stage=Usd.Stage.CreateNew(str((folder/"normal_udim.usda").resolve()))
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(stage,0.01)
    world=UsdGeom.Xform.Define(stage,"/World"); stage.SetDefaultPrim(world.GetPrim())
    def material(name,family="SS",texture=None,normal=False):
        path="/Looks/"+name
        mat=UsdShade.Material.Define(stage,path)
        preview=UsdShade.Shader.Define(stage,path+"/Preview"); preview.CreateIdAttr("UsdPreviewSurface")
        preview.CreateInput("diffuseColor",Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5))
        preview.CreateOutput("surface",Sdf.ValueTypeNames.Token); mat.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(),"surface")
        surface=UsdShade.Shader.Define(stage,path+"/Surface")
        surface.CreateIdAttr("ND_standard_surface_surfaceshader" if family=="SS" else "ND_open_pbr_surface_surfaceshader")
        surface.CreateInput("base_color",Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5))
        surface.CreateInput("specular" if family=="SS" else "specular_weight",Sdf.ValueTypeNames.Float).Set(0.0)
        surface.CreateInput("specular_roughness",Sdf.ValueTypeNames.Float).Set(0.7)
        surface.CreateOutput("out",Sdf.ValueTypeNames.Token); mat.CreateSurfaceOutput("mtlx").ConnectToSource(surface.ConnectableAPI(),"out")
        if texture:
            image=UsdShade.Shader.Define(stage,path+"/Image"); image.CreateIdAttr("ND_image_vector3" if normal else "ND_image_color3")
            image.CreateInput("file",Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture))
            image.GetInput("file").GetAttr().SetColorSpace("raw" if normal else "srgb_texture")
            image.CreateOutput("out",Sdf.ValueTypeNames.Vector3f if normal else Sdf.ValueTypeNames.Color3f)
            uv=UsdShade.Shader.Define(stage,path+"/UV"); uv.CreateIdAttr("ND_texcoord_vector2")
            uv.CreateInput("index",Sdf.ValueTypeNames.Int).Set(0); uv.CreateOutput("out",Sdf.ValueTypeNames.Float2)
            image.CreateInput("texcoord",Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),"out")
            if normal:
                node=UsdShade.Shader.Define(stage,path+"/Normal"); node.CreateIdAttr("ND_normalmap_float")
                node.CreateInput("in",Sdf.ValueTypeNames.Vector3f).ConnectToSource(image.ConnectableAPI(),"out")
                node.CreateInput("scale",Sdf.ValueTypeNames.Float).Set(0.7)
                node.CreateOutput("out",Sdf.ValueTypeNames.Vector3f)
                surface.CreateInput("normal" if family=="SS" else "geometry_normal",Sdf.ValueTypeNames.Vector3f).ConnectToSource(node.ConnectableAPI(),"out")
            else:
                surface.CreateInput("base" if family=="SS" else "base_weight",Sdf.ValueTypeNames.Float).Set(0.0)
                surface.CreateInput("emission" if family=="SS" else "emission_luminance",Sdf.ValueTypeNames.Float).Set(1.0)
                surface.CreateInput("emission_color",Sdf.ValueTypeNames.Color3f).ConnectToSource(image.ConnectableAPI(),"out")
        return mat
    def panel(name,x,z,size,mat,normal=(0,-1,0),uv_extent=1):
        half=size/2
        mesh=UsdGeom.Mesh.Define(stage,"/World/"+name)
        mesh.CreatePointsAttr([Gf.Vec3f(x-half,0,z-half),Gf.Vec3f(x+half,0,z-half),Gf.Vec3f(x+half,0,z+half),Gf.Vec3f(x-half,0,z+half)])
        mesh.CreateFaceVertexCountsAttr([3,3]); mesh.CreateFaceVertexIndicesAttr([0,1,2,0,2,3])
        mesh.CreateNormalsAttr([Gf.Vec3f(*normal)]*4); mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st",Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray([(0,0),(uv_extent,0),(uv_extent,uv_extent),(0,uv_extent)]))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    panel("UDIM",-300,210,200,material("UDIM",texture="color.<UDIM>.png"),uv_extent=2)
    panel("UDIM_Normal",340,210,200,material("UDIM_Normal",texture="normal_tiles.<UDIM>.png",normal=True),uv_extent=2)
    # Unbound materials retain cooked texture alternatives for runtime tests.
    material("Flat_Normal",texture="flat.png",normal=True)
    material("UDIM_Swap",texture="color_swap.<UDIM>.png")
    for family,z in (("SS",130),("OP",320)):
        panel(family+"_Normal",-70,z,140,material(family+"_Normal",family,texture="slope.png",normal=True))
        panel(family+"_Reference",110,z,140,material(family+"_Reference",family),normal=(0,-0.8,0.6))
    eye=Gf.Vec3d(0,-1200,220); target=Gf.Vec3d(0,0,220)
    camera=UsdGeom.Camera.Define(stage,"/Camera")
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(20.25); camera.CreateFocalLengthAttr(38)
    camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000))
    light=UsdLux.DistantLight.Define(stage,"/Key"); light.CreateIntensityAttr(2.0)
    light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(0,-500,700),target,Gf.Vec3d(0,0,1)).GetInverse())
    stage.GetRootLayer().Save()
    print(folder/"normal_udim.usda")


if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--output-directory",type=Path,default=Path(__file__).resolve().parent.parent/"samples/materialx/normal_udim_showcase")
    generate(p.parse_args().output_directory)
