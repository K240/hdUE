# /// script
# requires-python = ">=3.11,<3.12"
# ///
"""Generate the UV/normal test geometry and editable MaterialX sourceAsset inputs."""
import argparse
import json
import math
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

parser = argparse.ArgumentParser()
parser.add_argument("library", type=Path)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent/"samples/materialx/showcase.usda")
args = parser.parse_args()
library = json.loads(args.library.read_text(encoding="utf-8"))
if args.output.exists():
    raise FileExistsError(f"Choose a fresh stage path: {args.output}")
stage = Usd.Stage.CreateNew(str(args.output.resolve()))
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 0.01)
world = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(world.GetPrim())


def preview(path, color, roughness=0.45, metallic=0.0):
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path+"/Preview")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def mesh(path, points, indices, normals, uv):
    result = UsdGeom.Mesh.Define(stage, path)
    result.CreatePointsAttr(Vt.Vec3fArray(points))
    result.CreateFaceVertexCountsAttr([3]*(len(indices)//3))
    result.CreateFaceVertexIndicesAttr(indices)
    result.CreateNormalsAttr(Vt.Vec3fArray(normals))
    result.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    result.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    UsdGeom.PrimvarsAPI(result).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray(uv))
    return result


points, normals, uv, indices = [], [], [], []
rings, segments = 32, 64
for ring in range(rings+1):
    phi = math.pi*ring/rings
    for segment in range(segments+1):
        theta = 2*math.pi*segment/segments
        normal = Gf.Vec3f(math.sin(phi)*math.cos(theta), math.sin(phi)*math.sin(theta), math.cos(phi))
        points.append(normal*50)
        normals.append(normal)
        uv.append(Gf.Vec2f(segment/segments, 1-ring/rings))
for ring in range(rings):
    for segment in range(segments):
        a = ring*(segments+1)+segment
        b = a+segments+1
        if ring < rings-1:
            indices.extend((a, b, b+1))
        if ring > 0:
            indices.extend((a, b+1, a+1))
grey = preview("/Looks/Grey", (0.38,0.38,0.38), 0.65)
white = preview("/Looks/White", (0.7,0.7,0.7), 0.65)
for entry in library["materials"]:
    name = entry["material"]
    family, kind = name.split("_", 1)
    column = ("Metal", "Coat", "Anisotropy", "Glass", "Subsurface").index(kind)
    row = 0 if family == "SS" else 1
    material = preview("/Looks/"+name, (0.7,0.27,0.07), metallic=0.95 if kind in ("Metal","Anisotropy") else 0.02)
    shader = UsdShade.Shader.Define(stage, str(material.GetPath())+"/MaterialX")
    shader.SetSourceAsset(Sdf.AssetPath(Path(entry["source"]).name), "mtlx")
    shader.SetSourceAssetSubIdentifier(name, "mtlx")
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput("mtlx").ConnectToSource(shader.ConnectableAPI(), "surface")
    for field, p in entry["bindings"].items():
        value = p["default"]
        vector3 = p["type"] == "vector" and p.get("source_type") in ("color3", "vector3")
        value_type = Sdf.ValueTypeNames.Float if p["type"] == "scalar" else (Sdf.ValueTypeNames.Color3f if vector3 else Sdf.ValueTypeNames.Color4f) if p["type"] == "vector" else Sdf.ValueTypeNames.String
        shader.CreateInput(field, value_type).Set((Gf.Vec3f(*value[:3]) if vector3 else Gf.Vec4f(*value)) if p["type"] == "vector" else value)
    sphere = mesh("/World/"+name, points, indices, normals, uv)
    sphere.AddTranslateOp().Set(Gf.Vec3d((column-2)*140, 0, 65+row*145))
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    x, z = (column-2)*140, row*145
    panel = mesh("/World/Backdrop_"+name,
        [Gf.Vec3f(x-70,80,z),Gf.Vec3f(x+70,80,z),Gf.Vec3f(x+70,80,z+145),Gf.Vec3f(x-70,80,z+145)],
        [0,1,2,0,2,3], [Gf.Vec3f(0,-1,0)]*4,
        [Gf.Vec2f(0,0),Gf.Vec2f(1,0),Gf.Vec2f(1,1),Gf.Vec2f(0,1)])
    UsdShade.MaterialBindingAPI.Apply(panel.GetPrim()).Bind(white if (column+row)%2 else grey)
floor = mesh("/World/Floor", [Gf.Vec3f(-400,-150,0),Gf.Vec3f(400,-150,0),Gf.Vec3f(400,160,0),Gf.Vec3f(-400,160,0)],
    [0,1,2,0,2,3], [Gf.Vec3f(0,0,1)]*4, [Gf.Vec2f(0,0),Gf.Vec2f(1,0),Gf.Vec2f(1,1),Gf.Vec2f(0,1)])
UsdShade.MaterialBindingAPI.Apply(floor.GetPrim()).Bind(grey)
target = Gf.Vec3d(0,0,135)
camera = UsdGeom.Camera.Define(stage, "/Camera")
eye = Gf.Vec3d(300,-1100,400)
camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
camera.CreateHorizontalApertureAttr(36)
camera.CreateVerticalApertureAttr(20.25)
camera.CreateFocalLengthAttr(40)
camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000))
camera.CreateFocusDistanceAttr((eye-target).GetLength())
for name, position, intensity, size in (
        ("Key", Gf.Vec3d(-250,-350,500), 9000, (300,150)),
        ("Fill", Gf.Vec3d(450,-200,250), 4500, (150,250))):
    light = UsdLux.RectLight.Define(stage, "/"+name)
    light.CreateIntensityAttr(intensity)
    light.CreateWidthAttr(size[0])
    light.CreateHeightAttr(size[1])
    light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(position,target,Gf.Vec3d(0,0,1)).GetInverse())
# UE's realtime Substrate direct-light evaluation disables anisotropy for
# Rect lights. Include an analytic light so both renderers exercise it.
analytic = UsdLux.DistantLight.Define(stage, "/AnisotropyKey")
analytic.CreateIntensityAttr(2.0)
analytic.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(-250,-350,500),target,Gf.Vec3d(0,0,1)).GetInverse())
stage.GetRootLayer().Save()
print(args.output)
