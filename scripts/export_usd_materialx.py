# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["MaterialX==1.39.4"]
# ///
"""Export supported composed USD MaterialX graphs to UE's MaterialX version."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import MaterialX as mx
from pxr import Sdf, Usd, UsdShade
from usd_materialx_graph import graph_data
from materialx_textures import udim_tiles

# Deliberately bounded to nodes exercised by this bridge. A known MaterialX
# nodedef alone does not prove UE's importer supports the corresponding node.
NODEDEFS={"ND_standard_surface_surfaceshader","ND_open_pbr_surface_surfaceshader",
          "ND_image_color3","ND_image_vector3","ND_normalmap_float",
          "ND_texcoord_vector2","ND_multiply_color3","ND_image_float",
          "ND_multiply_float","ND_invert_float"}


def texture_path(stage,item,time,embedded=None):
    value=item["value"]
    if not isinstance(value,dict):
        raise ValueError(f"Texture needs an asset input: {item['attribute']}")
    raw=value["asset"]
    if raw.startswith('opdef:'):
        entry=(embedded or {}).get(raw)
        if not entry:
            raise ValueError('Embedded texture needs Houdini preparation: '+raw)
        file=Path(entry['path']).resolve()
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('Embedded texture changed; resolve again: '+raw)
        return str(file),None
    if "<UDIM>" not in raw:
        if not value.get("resolved"):
            raise ValueError(f"Texture is unresolved: {item['attribute']}")
        file=Path(value["resolved"]).resolve()
        match=re.fullmatch(r"(.+[._])\d{4}(\.[^.]+)",file.name)
        if match:
            pattern=str(file.parent/(match[1]+"<UDIM>"+match[2]))
            try:
                tiles={str(k):v for k,v in udim_tiles(pattern).items()}
            except ValueError:
                # A literal file such as photo.2020.png or a lone 1003 file
                # remains a normal 2D image. Actual multi-block UE imports
                # are checked again from their source-block receipt.
                tiles={}
            if len(tiles)>1:
                return str(file),dict(pattern=pattern,tiles=tiles)
        return str(file),None
    if Path(raw).is_absolute():
        pattern=str(Path(raw).resolve())
    else:
        attr=stage.GetAttributeAtPath(item["attribute"])
        layer=next((s.layer for s in attr.GetPropertyStack(time)
                    if s.HasDefaultValue() or s.layer.ListTimeSamplesForPath(s.path)),None)
        if layer is None:
            raise ValueError(f"Cannot anchor UDIM asset to its USD layer: {item['attribute']}")
        anchored=Path(Sdf.ComputeAssetPathRelativeToLayer(layer,raw))
        # The USD resolver returns an unresolved search path for <UDIM>,
        # since the pattern itself is not a real file. Anchor it explicitly.
        if not anchored.is_absolute():
            if not layer.realPath or not Path(layer.realPath).is_file():
                raise ValueError(f"UDIM requires a filesystem USD layer: {item['attribute']}")
            anchored=Path(layer.realPath).parent/raw
        pattern=str(anchored.resolve())
    tiles={str(k):v for k,v in udim_tiles(pattern).items()}
    return pattern,dict(pattern=pattern,tiles=tiles)


def export(source, output, time=Usd.TimeCode.Default(), texture_manifest=None):
    source=source.resolve()
    if output.exists():
        raise FileExistsError(f"Choose a fresh export directory: {output}")
    stage=Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError(f"Cannot open USD: {source}")
    definitions=mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(),mx.getDefaultDataSearchPath(),definitions)
    doc=mx.createDocument(); doc.setColorSpace("lin_rec709")
    entries=[]; texture_sets={}; texture_roles={}
    dependencies={}
    embedded={}
    if texture_manifest:
        manifest=json.loads(texture_manifest.read_text(encoding='utf-8-sig'))
        if manifest.get('version')!=1:
            raise ValueError('Unsupported embedded texture manifest')
        embedded=manifest['textures']
        dependencies[str(texture_manifest.resolve())]=hashlib.sha256(texture_manifest.read_bytes()).hexdigest()
    for layer in stage.GetUsedLayers():
        if layer.realPath:
            file=Path(layer.realPath).resolve()
            dependencies[str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        material=UsdShade.Material(prim)
        if not material.GetSurfaceOutput("mtlx"):
            continue
        # Keep the external sourceAsset route separate and unchanged.
        surface=material.ComputeSurfaceSource("mtlx")[0]
        if surface and surface.GetImplementationSource()==UsdShade.Tokens.sourceAsset:
            continue
        graph=graph_data(material,time)
        for path,node in graph["nodes"].items():
            if node["id"] not in NODEDEFS:
                raise ValueError(f"Unsupported MaterialX nodedef: {path}: {node['id']}")
        normal_images=set()
        for path,node in graph["nodes"].items():
            if node["id"]=="ND_normalmap_float":
                if any(name in node["inputs"] for name in ("normal","tangent","bitangent")):
                    raise ValueError(f"Custom normalmap tangent frames are unsupported: {path}")
                source_input=node["inputs"].get("in",{})
                source_node=graph["nodes"].get(source_input.get("node"),{})
                if source_node.get("id")!="ND_image_vector3":
                    raise ValueError(f"normalmap needs a direct ND_image_vector3 source: {path}")
                normal_images.add(source_input["node"])
        if any(n["id"]=="ND_image_vector3" and p not in normal_images for p,n in graph["nodes"].items()):
            raise ValueError("ND_image_vector3 is currently supported only as a normalmap source")
        tag=hashlib.sha1((str(source)+str(prim.GetPath())).encode()).hexdigest()[:10]
        material_name=re.sub(r"[^A-Za-z0-9_]","_",prim.GetName())+"_"+tag
        names={path:material_name+"_n"+str(i) for i,path in enumerate(graph["nodes"])}
        exported={}; fields=[]
        for path,node in graph["nodes"].items():
            definition=definitions.getNodeDef(node["id"])
            if definition is None or node["id"] not in NODEDEFS:
                raise ValueError(f"Unsupported MaterialX nodedef: {path}: {node['id']}")
            if definition.getType()=="multioutput":
                raise ValueError(f"Multi-output MaterialX nodes need explicit support: {path}")
            exported[path]=doc.addNode(definition.getNodeString(),names[path],definition.getType())
            exported[path].setNodeDefString(node["id"])
            node["mx_name"]=names[path]
        terminal=graph["terminal"]["node"]
        if exported[terminal].getCategory() not in {"standard_surface","open_pbr_surface"}:
            raise ValueError(f"Unsupported MaterialX surface: {terminal}")
        for item in [graph["terminal"]]+[v for n in graph["nodes"].values() for v in n["inputs"].values() if "node" in v]:
            definition=definitions.getNodeDef(graph["nodes"][item["node"]]["id"])
            valid_outputs={p.getName() for p in definition.getActiveOutputs()}
            if definition.getType()=="surfaceshader":
                valid_outputs.add("surface")
            if item["output"] not in valid_outputs:
                raise ValueError(f"Unknown MaterialX output: {item['node']}.outputs:{item['output']}")
        for path,node in graph["nodes"].items():
            definition=definitions.getNodeDef(node["id"])
            for name,item in node["inputs"].items():
                declared=definition.getActiveInput(name)
                if declared is None:
                    raise ValueError(f"Unknown MaterialX input: {path}.inputs:{name}")
                port=exported[path].addInput(name,declared.getType())
                if item.get("colorspace"):
                    port.setColorSpace(item["colorspace"])
                if "node" in item:
                    upstream=exported[item["node"]]
                    # USD calls a single surface output either surface or out.
                    if upstream.getType()!=declared.getType():
                        raise ValueError(f"MaterialX connection type mismatch: {path}.inputs:{name}")
                    port.setNodeName(upstream.getName())
                elif item["value"] is not None:
                    value=item["value"]
                    if exported[path].getCategory()=="texcoord" and name=="index" and value!=0:
                        raise ValueError(f"Only UV set 0 is supported: {item['attribute']}")
                    if declared.getType()=="filename":
                        filename,tiles=texture_path(stage,item,time,embedded)
                        sources=list(tiles["tiles"].values()) if tiles else [filename]
                        for source_file in sources:
                            texture=Path(source_file)
                            if not texture.is_file():
                                raise ValueError(f"Texture is missing: {texture}")
                            dependencies[str(texture)]=hashlib.sha256(texture.read_bytes()).hexdigest()
                        if tiles:
                            texture_sets[tiles["pattern"]]=tiles
                        normal=path in normal_images
                        role="normal" if normal else "color"
                        role_key=tiles["pattern"] if tiles else filename
                        if role_key in texture_roles and texture_roles[role_key]!=role:
                            raise ValueError(f"Separate normal/color source files are required for UE texture sampling: {filename}")
                        texture_roles[role_key]=role
                        colorspace=item.get("colorspace")
                        if normal:
                            if colorspace not in (None,"","raw","lin_rec709","lin_rec709_scene"):
                                raise ValueError(f"normalmap image must use raw/linear data: {item['attribute']}")
                            colorspace="raw"
                        value=filename.replace("\\","/")
                        if colorspace:
                            port.setColorSpace(str(colorspace))
                    text=", ".join(str(v) for v in value) if isinstance(value,list) else str(value).lower() if isinstance(value,bool) else str(value)
                    port.setValueString(text)
                    fields.append(dict(node=names[path],input=name,attribute=item["attribute"],type=declared.getType()))
                else:
                    exported[path].removeInput(name)
        m=doc.addNode("surfacematerial",material_name,"material")
        m.addInput("surfaceshader","surfaceshader").setNodeName(names[terminal])
        # mx_name is export metadata, not part of the USD graph signature.
        for node in graph["nodes"].values():
            node.pop("mx_name",None)
        entries.append(dict(material=material_name,usd_material=str(prim.GetPath()),usd_shader=terminal,graph=graph,fields=fields))
    if not entries:
        raise ValueError("No supported inline MaterialX materials were found")
    # Validate against the same nodedef version used by UE; don't copy stdlib
    # nodes into the exported document.
    validation=mx.createDocument(); validation.importLibrary(definitions)
    mx.readFromXmlString(validation,mx.writeToXmlString(doc))
    valid,message=validation.validate()
    if not valid:
        raise ValueError(f"MaterialX validation failed: {message}")
    output.mkdir(parents=True)
    file=output/("UsdMaterials_"+hashlib.sha1(str(source).encode()).hexdigest()[:10]+".mtlx")
    # MaterialX writes literal <UDIM> inside attributes. Escape it so the
    # document also conforms to strict XML readers used by registry tooling.
    file.write_text(mx.writeToXmlString(doc).replace("<UDIM>","&lt;UDIM&gt;"),encoding="utf-8")
    manifest=dict(version=1,usd_stage=str(source),source=str(file.resolve()),time=None if time.IsDefault() else time.GetValue(),materials=entries,dependencies=dependencies,texture_sets=list(texture_sets.values()),embedded_textures=embedded)
    (output/"export.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(f"{file}: {len(entries)} inline USD MaterialX materials")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--time",type=float)
    parser.add_argument("--texture-manifest",type=Path)
    args=parser.parse_args()
    export(args.source,args.output,Usd.TimeCode(args.time) if args.time is not None else Usd.TimeCode.Default(),args.texture_manifest)
