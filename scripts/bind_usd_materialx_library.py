# /// script
# requires-python = ">=3.11"
# ///
"""Attach original USD attribute/graph identities to a native UE registry."""
import argparse
import hashlib
import json
from pathlib import Path
# graph_signature is JSON-only, but its module also uses pxr for graph reads.
from usd_materialx_graph import graph_signature
from materialx_textures import validate_texture_sets


def bind(export_file, library_file, output):
    export=json.loads(export_file.read_text(encoding="utf-8-sig"))
    library=json.loads(library_file.read_text(encoding="utf-8-sig"))
    validate_texture_sets(export.get("texture_sets",[]))
    entries={p["material"]:p for p in export["materials"]}
    for file,expected in export["dependencies"].items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest()!=expected:
            raise ValueError(f"USD export dependency changed; prepare again: {file}")
    for entry in library["materials"]:
        original=entries.pop(entry["material"])
        parameters={p["name"]:p for p in entry["parameters"]}
        bindings=[]
        for field in original["fields"]:
            name=field["node"]+"_"+field["input"]
            p=parameters.get(name)
            # Integer/bool/unit selections can change shader topology.
            if p and field["type"] in {"float","color3","color4","vector2","vector3","vector4"} and p["type"] in {"scalar","vector"}:
                bindings.append(dict(attribute=field["attribute"],parameter=name,type=p["type"],default=p["default"]))
            if field["type"]=="filename":
                p=parameters.get(field["node"])
                if p and p["type"]=="texture":
                    image_path=field["attribute"].split(".inputs:")[0]
                    bindings.append(dict(attribute=image_path+".inputs:hdue:texture",parameter=p["name"],type="texture",default=p["default"]))
        for alias,p in entry["bindings"].items():
            if alias.startswith("hdue:") and p["type"]=="texture":
                bindings.append(dict(attribute=original["usd_shader"]+".inputs:"+alias,parameter=p["parameter"],type="texture",default=p["default"]))
        entry.update(usd_stage=export["usd_stage"],usd_material=original["usd_material"],usd_shader=original["usd_shader"],
                     usd_bindings=bindings,usd_graph_signature=graph_signature(original["graph"],[p["attribute"] for p in bindings]))
    if entries:
        raise ValueError("Some USD materials were not imported")
    library["dependencies"].update(export["dependencies"])
    output.write_text(json.dumps(library,indent=2),encoding="utf-8")
    print(f"{output}: {len(library['materials'])} USD MaterialX bindings")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("export",type=Path); p.add_argument("library",type=Path); p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(); bind(args.export,args.library,args.output)
