# /// script
# requires-python = ">=3.11"
# ///
"""Build the host's MaterialX-to-cooked-asset registry from UE import receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import re
from materialx_textures import udim_tiles


def build(reports, destination):
    materials = []
    dependencies = {}; texture_sets={}
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        if report.get("substrate") != 1 or report.get("gbuffer_format") != 1:
            raise ValueError("MaterialX import was not performed with Substrate Adaptive GBuffer")
        source = Path(report["source"]).resolve()
        if report.get("source_sha1") != hashlib.sha1(source.read_bytes()).hexdigest():
            raise ValueError(f"Import receipt is stale; import MaterialX again: {source}")
        for name, expected in report["dependencies_sha1"].items():
            file = Path(name).resolve()
            if not file.is_file() or hashlib.sha1(file.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Imported texture/source changed; import again: {file}")
            dependencies[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
        dependencies[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        root = ET.fromstring(source.read_text(encoding="utf-8-sig").replace("<UDIM>","&lt;UDIM&gt;"))
        for asset in report["assets"]:
            for p in asset.get("parameters",[]):
                blocks=p.get("source_blocks",[])
                if len(blocks)>1:
                    if not p.get("virtual_texture"):
                        raise ValueError(f"UDIM was not imported as a virtual texture: {p['name']}")
                    first=blocks[0]
                    for block in blocks:
                        w,h=block["width"],block["height"]
                        if w<1 or h<1 or w&(w-1) or h&(h-1) or w*first["height"]!=h*first["width"]:
                            raise ValueError(f"UE UDIM blocks need power-of-two dimensions and a common aspect ratio: {p['name']}")
                    first_file=Path(p["source_files"][0]).resolve()
                    match=re.fullmatch(r"(.+[._])\d{4}(\.[^.]+)",first_file.name)
                    if not match:
                        raise ValueError(f"Cannot identify imported UDIM source set: {p['name']}")
                    pattern=str(first_file.parent/(match[1]+"<UDIM>"+match[2]))
                    tiles={str(k):v for k,v in udim_tiles(pattern).items()}
                    positions={str(1001+b["x"]+10*b["y"]) for b in blocks}
                    if set(tiles)!=positions:
                        raise ValueError(f"UDIM source membership differs from imported blocks: {p['name']}")
                    texture_sets[pattern]=dict(pattern=pattern,tiles=tiles)
        nodes = {node.get("name"): node for node in root.iter() if node.get("name")}
        texture_parameters={p["name"]:p for a in report["assets"] for p in a.get("parameters",[]) if p["type"]=="texture"}
        for node in root.iter():
            if node.tag=="normalmap":
                source_input=node.find("./input[@name='in']")
                if source_input is not None and source_input.get("nodename") in texture_parameters:
                    p=texture_parameters[source_input.get("nodename")]
                    if not p.get("normal_map") or p.get("srgb") or not p.get("flip_green"):
                        raise ValueError(f"MaterialX normal texture has incorrect compression/color/green settings: {p['name']}")
            file_input=node.find("./input[@name='file']")
            if file_input is not None and "<UDIM>" in file_input.get("value",""):
                pattern=str((source.parent/file_input.get("value")).resolve())
                expected=set(udim_tiles(pattern))
                p=texture_parameters.get(node.get("name"))
                if not p:
                    raise ValueError(f"UDIM image has no cooked texture parameter: {node.get('name')}")
                actual={1001+b["x"]+10*b["y"] for b in p.get("source_blocks",[])}
                if actual!=expected:
                    raise ValueError(f"Imported UDIM block positions do not match source tiles: {node.get('name')}")
        for node in root.iter("input"):
            if node.get("type") == "filename" and node.get("value"):
                file = (source.parent / node.get("value")).resolve()
                files=[str(file)]
                if "<UDIM>" in str(file):
                    tiles={str(k):v for k,v in udim_tiles(str(file)).items()}
                    texture_sets[str(file)]=dict(pattern=str(file),tiles=tiles)
                    files=list(tiles.values())
                for name in files:
                    if not Path(name).is_file() or name not in dependencies:
                        raise ValueError(f"Texture is missing from the import receipt; import again: {name}")
        count = 0
        for asset in report["assets"]:
            if not asset.get("native_substrate"):
                continue
            material = nodes.get(asset["name"])
            if material is None or material.tag != "surfacematerial":
                raise ValueError(f"Cannot identify MaterialX source for {asset['name']}")
            link = material.find("./input[@name='surfaceshader']")
            shader = nodes.get(link.get("nodename")) if link is not None else None
            if shader is None:
                raise ValueError(f"Cannot identify the surface shader for {asset['name']}")
            parameters = {p["name"]: p for p in asset["parameters"]}
            bindings = {}
            for field in shader.findall("input"):
                name = field.get("name")
                target = shader.get("name") + "_" + name
                if target in parameters:
                    p = parameters[target]
                    bindings[name] = dict(parameter=target, type=p["type"], source_type=field.get("type"), default=p["default"])
                elif field.get("nodename") in parameters:
                    # A texture changes through a cooked UE Texture parameter;
                    # it is an explicit hdUE override on the sourceAsset shader.
                    p = parameters[field.get("nodename")]
                    if p["type"] == "texture":
                        bindings["hdue:" + name + "_texture"] = dict(parameter=p["name"], type="texture", default=p["default"])
            materials.append(dict(source=str(source), material=asset["name"], shader=shader.get("name"),
                model=shader.tag, asset=asset["asset"], blend_mode=asset["blend_mode"],
                bindings=bindings, parameters=asset["parameters"]))
            count += 1
        if count != report["materials"]:
            raise ValueError("Incomplete MaterialX import receipt")
    destination.write_text(json.dumps(dict(version=1, materials=materials, dependencies=dependencies,texture_sets=list(texture_sets.values())), indent=2), encoding="utf-8")
    print(f"{destination}: {len(materials)} native Substrate materials")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.reports, args.output)
