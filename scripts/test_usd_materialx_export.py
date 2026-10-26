# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["MaterialX==1.39.4"]
# ///
"""Adversarial graph/export cases, using real USD and MaterialX validation."""
import argparse
import json
from pathlib import Path
import shutil
from pxr import Gf, Sdf, Usd, UsdShade
from export_usd_materialx import export
from usd_materialx_graph import graph_data,graph_signature
from materialx_textures import udim_tiles,validate_texture_sets


def run(output):
    if output.exists():
        raise FileExistsError("Choose fresh graph-test evidence")
    output.mkdir(parents=True)
    cases=[]
    def fixture(name):
        folder=output/name; folder.mkdir()
        stage=Usd.Stage.CreateNew(str((folder/"scene.usda").resolve()))
        material=UsdShade.Material.Define(stage,"/Looks/Test")
        shader=UsdShade.Shader.Define(stage,"/Looks/Test/Surface")
        shader.CreateIdAttr("ND_standard_surface_surfaceshader")
        shader.CreateInput("base_color",Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.6,0.2,0.1))
        shader.CreateInput("specular_roughness",Sdf.ValueTypeNames.Float).Set(0.4)
        shader.CreateOutput("out",Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput("mtlx").ConnectToSource(shader.ConnectableAPI(),"out")
        return folder,stage,material,shader
    def image(stage,shader):
        node=UsdShade.Shader.Define(stage,"/Looks/Test/Image")
        node.CreateIdAttr("ND_image_color3")
        node.CreateInput("file",Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("missing.png"))
        node.CreateOutput("out",Sdf.ValueTypeNames.Color3f)
        shader.GetInput("base_color").ConnectToSource(node.ConnectableAPI(),"out")
        return node
    def bad(name,mutate,expected):
        folder,stage,material,shader=fixture(name)
        mutate(stage,material,shader)
        stage.GetRootLayer().Save()
        try:
            export(folder/"scene.usda",folder/"export")
        except ValueError as error:
            if expected.lower() not in str(error).lower():
                raise AssertionError(f"{name}: unexpected error: {error}") from error
            if (folder/"export").exists():
                raise AssertionError("A rejected graph left a usable export")
            cases.append(dict(case=name,rejected=True,error=str(error)))
        else:
            raise AssertionError(f"Invalid graph accepted: {name}")
    bad("unknown-node",lambda s,m,n:n.GetIdAttr().Set("ND_NoSuchNode"),"Unsupported")
    bad("unsupported-node",lambda s,m,n:n.GetIdAttr().Set("ND_noise3d_float"),"Unsupported")
    bad("shader-cycle",lambda s,m,n:n.GetInput("base_color").ConnectToSource(n.ConnectableAPI(),"out"),"Cyclic")
    bad("dangling-link",lambda s,m,n:n.GetInput("base_color").GetAttr().SetConnections([Sdf.Path("/Missing.outputs:out")]),"Missing")
    bad("multiple-links",lambda s,m,n:n.GetInput("base_color").GetAttr().SetConnections([Sdf.Path("/A.outputs:out"),Sdf.Path("/B.outputs:out")]),"Multiple")
    bad("unknown-port",lambda s,m,n:n.CreateInput("not_in_schema",Sdf.ValueTypeNames.Float).Set(0.5),"Unknown")
    def unknown_output(s,m,n):
        n.CreateOutput("bogus",Sdf.ValueTypeNames.Token)
        m.GetSurfaceOutput("mtlx").ConnectToSource(n.ConnectableAPI(),"bogus")
    bad("unknown-output",unknown_output,"Unknown")
    bad("missing-texture",lambda s,m,n:image(s,n),"unresolved")
    def bad_uv(s,m,n):
        tex=image(s,n)
        tex.GetInput("file").Set(Sdf.AssetPath(str((Path(__file__).resolve().parent.parent/"samples/materialx/textures/warm.png").resolve())))
        uv=UsdShade.Shader.Define(s,"/Looks/Test/UV")
        uv.CreateIdAttr("ND_texcoord_vector2"); uv.CreateInput("index",Sdf.ValueTypeNames.Int).Set(1)
        uv.CreateOutput("out",Sdf.ValueTypeNames.Float2)
        tex.CreateInput("texcoord",Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),"out")
    bad("uv-set-one",bad_uv,"UV set 0")
    def wrong_type(s,m,n):
        uv=UsdShade.Shader.Define(s,"/Looks/Test/UV"); uv.CreateIdAttr("ND_texcoord_vector2")
        uv.CreateOutput("out",Sdf.ValueTypeNames.Float2)
        n.GetInput("base_color").ConnectToSource(uv.ConnectableAPI(),"out")
    bad("connection-type",wrong_type,"type mismatch")
    bad("nonfinite",lambda s,m,n:n.GetInput("specular_roughness").Set(float("nan")),"Nonfinite")
    def interface_cycle(s,m,n):
        a=m.CreateInput("a",Sdf.ValueTypeNames.Color3f); b=m.CreateInput("b",Sdf.ValueTypeNames.Color3f)
        a.ConnectToSource(b); b.ConnectToSource(a); n.GetInput("base_color").ConnectToSource(a)
    bad("interface-cycle",interface_cycle,"Cyclic")
    bad("volume-terminal",lambda s,m,n:m.CreateVolumeOutput("mtlx").ConnectToSource(n.ConnectableAPI(),"out"),"displacement/volume")
    def normal(s,m,n,colorspace="raw",custom=False):
        tex=image(s,n); tex.GetIdAttr().Set("ND_image_vector3")
        tex.GetOutput("out").GetAttr().SetTypeName(Sdf.ValueTypeNames.Vector3f)
        tex.GetInput("file").Set(Sdf.AssetPath(str((Path(__file__).resolve().parent.parent/"samples/materialx/normal_udim_showcase/slope.png").resolve())))
        tex.GetInput("file").GetAttr().SetColorSpace(colorspace)
        node=UsdShade.Shader.Define(s,"/Looks/Test/Normal"); node.CreateIdAttr("ND_normalmap_float")
        node.CreateInput("in",Sdf.ValueTypeNames.Vector3f).ConnectToSource(tex.ConnectableAPI(),"out")
        node.CreateInput("scale",Sdf.ValueTypeNames.Float).Set(0.7)
        node.CreateOutput("out",Sdf.ValueTypeNames.Vector3f)
        n.GetInput("base_color").ClearSources()
        n.CreateInput("normal",Sdf.ValueTypeNames.Vector3f).ConnectToSource(node.ConnectableAPI(),"out")
        if custom: node.CreateInput("tangent",Sdf.ValueTypeNames.Vector3f).Set(Gf.Vec3f(1,0,0))
        return node
    bad("normal-srgb",lambda s,m,n:normal(s,m,n,"srgb_texture"),"raw/linear")
    bad("normal-custom-basis",lambda s,m,n:normal(s,m,n,custom=True),"tangent frames")
    bad("normal-vector2-scale",lambda s,m,n:normal(s,m,n).GetIdAttr().Set("ND_normalmap_vector2"),"Unsupported")
    def tiled(s,m,n,indices=(1001,1002,1011,1012),filename="tiles.<UDIM>.png"):
        folder=Path(s.GetRootLayer().realPath).parent
        for index in indices:
            shutil.copyfile(Path(__file__).resolve().parent.parent/"samples/materialx/textures/warm.png",folder/f"tiles.{index}.png")
        tex=image(s,n); tex.GetInput("file").Set(Sdf.AssetPath(filename))
    bad("udim-empty",lambda s,m,n:tiled(s,m,n,()),"no tiles")
    bad("udim-off-origin-single",lambda s,m,n:tiled(s,m,n,(1003,)),"off-origin")
    bad("udim-out-of-range",lambda s,m,n:tiled(s,m,n,(1001,1999)),"1001..1910")
    bad("udim-name",lambda s,m,n:tiled(s,m,n,filename="tiles<UDIM>.png"),"BaseName")
    # Layer anchoring, standard coordinate numbering, and membership changes.
    folder,stage,material,shader=fixture("udim-membership")
    tiled(stage,material,shader); stage.GetRootLayer().Save()
    manifest=export(folder/"scene.usda",folder/"export")
    record=manifest["texture_sets"][0]
    if set(record["tiles"])!={"1001","1002","1011","1012"}:
        raise AssertionError("UDIM numbering/anchoring was lost")
    validate_texture_sets([record])
    shutil.copyfile(folder/"tiles.1001.png",folder/"tiles.1003.png")
    try:
        validate_texture_sets([record])
    except ValueError:
        pass
    else:
        raise AssertionError("New UDIM tiles did not invalidate the cooked membership")
    cases.append(dict(case="udim-anchoring-and-membership",passed=True))
    folder,stage,material,shader=fixture("udim-concrete-filename")
    tiled(stage,material,shader,filename="tiles.1001.png"); stage.GetRootLayer().Save()
    manifest=export(folder/"scene.usda",folder/"export")
    if len(manifest["texture_sets"])!=1 or len(manifest["texture_sets"][0]["tiles"])!=4:
        raise AssertionError("Concrete UDIM seed filename did not capture all tiles")
    cases.append(dict(case="udim-concrete-filename",passed=True))
    for filename in ("image.1003.png","photo.2020.png"):
        folder,stage,material,shader=fixture("literal-"+filename)
        shutil.copyfile(Path(__file__).resolve().parent.parent/"samples/materialx/textures/warm.png",folder/filename)
        node=image(stage,shader); node.GetInput("file").Set(Sdf.AssetPath(filename)); stage.GetRootLayer().Save()
        manifest=export(folder/"scene.usda",folder/"export")
        if manifest["texture_sets"]:
            raise AssertionError("A lone literal numbered image was incorrectly treated as UDIM")
        cases.append(dict(case="literal-"+filename,passed=True))
    # A resolved relative Unicode/space-containing asset must remain absolute
    # in the generated MaterialX, independent of its output directory.
    folder,stage,material,shader=fixture("asset-resolution")
    texture=folder/"画像 space.png"
    shutil.copyfile(Path(__file__).resolve().parent.parent/"samples/materialx/textures/warm.png",texture)
    tex=image(stage,shader); tex.GetInput("file").Set(Sdf.AssetPath(texture.name))
    stage.GetRootLayer().Save()
    manifest=export(folder/"scene.usda",folder/"export")
    if str(texture.resolve()) not in manifest["dependencies"]:
        raise AssertionError("Texture source was not resolved relative to its USD layer")
    # Runtime cache signatures must allow exposed values and reject structure
    # and unexposed values, including interface forwarding changes.
    before=graph_data(material); mutable=[str(shader.GetInput("specular_roughness").GetAttr().GetPath())]
    shader.GetInput("specular_roughness").Set(0.8)
    after=graph_data(material)
    if graph_signature(before,mutable)!=graph_signature(after,mutable) or graph_signature(before)==graph_signature(after):
        raise AssertionError("Editable-value signature contract failed")
    shader.GetIdAttr().Set("ND_open_pbr_surface_surfaceshader")
    if graph_signature(before,mutable)==graph_signature(graph_data(material),mutable):
        raise AssertionError("Changed shader identity used stale cooked data")
    cases.append(dict(case="resolved-asset-and-signatures",passed=True))
    result=dict(passed=True,cases=cases,rejections=sum(p.get("rejected",False) for p in cases))
    (output/"verified.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(f"Verified USD MaterialX export: {len(cases)} cases: {output}")


if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--output",required=True,type=Path)
    run(p.parse_args().output)
