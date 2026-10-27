"""Read a composed UsdShade graph without loading MaterialX into the viewer."""
import hashlib
import json
import math
from pxr import Sdf, Usd, UsdShade


def value_data(value):
    if isinstance(value, Sdf.AssetPath):
        # Houdini resolves opdef: to the URI itself; standalone OpenUSD leaves
        # it unresolved. Its identity must be independent of the host resolver.
        return {"asset":value.path,"resolved":'' if value.path.startswith('opdef:') else value.resolvedPath}
    if value is None or isinstance(value,(str,bool,int)):
        return value
    if isinstance(value,float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite MaterialX input")
        return value
    return [value_data(v) for v in value]


def graph_data(material, time=Usd.TimeCode.Default()):
    """Flatten NodeGraph interfaces, retaining all connection paths for checks."""
    stage=material.GetPrim().GetStage()
    nodes={}
    visiting=set()
    for terminal in (material.GetDisplacementOutput("mtlx"),material.GetVolumeOutput("mtlx")):
        if terminal and terminal.GetAttr().GetConnections():
            raise ValueError(f"Unsupported MaterialX displacement/volume: {terminal.GetAttr().GetPath()}")
    def resolve(attr, seen=()):
        path=str(attr.GetPath())
        if path in seen:
            raise ValueError(f"Cyclic MaterialX interface: {path}")
        links=attr.GetConnections()
        if len(links)>1:
            raise ValueError(f"Multiple MaterialX sources: {path}")
        if links:
            source=stage.GetAttributeAtPath(links[0])
            if not source:
                raise ValueError(f"Missing MaterialX connection: {path} -> {links[0]}")
            if source.GetPrim().IsA(UsdShade.Shader) and source.GetName().startswith("outputs:"):
                shader=UsdShade.Shader(source.GetPrim())
                visit(shader)
                return dict(node=str(shader.GetPath()),output=source.GetBaseName(),trace=[path,str(links[0])])
            result=resolve(source,seen+(path,))
            return dict(result,trace=[path]+result["trace"])
        value=attr.Get(time)
        original=attr.GetCustomDataByKey('hdueOpTexture')
        if original and original.get('uri','').startswith('opdef:'):
            current=value.path if isinstance(value,Sdf.AssetPath) else value
            if current==original['resolved']:
                value=Sdf.AssetPath(original['uri'],original['uri'])
        return dict(attribute=path,type=str(attr.GetTypeName()),value=value_data(value),colorspace=str(attr.GetColorSpace()),trace=[path])
    def visit(shader):
        path=str(shader.GetPath())
        if path in visiting:
            raise ValueError(f"Cyclic MaterialX shader: {path}")
        if path in nodes:
            return
        visiting.add(path)
        if shader.GetImplementationSource()!=UsdShade.Tokens.id:
            raise ValueError(f"Inline MaterialX needs an info:id implementation: {path}")
        identity=shader.GetIdAttr().Get()
        if not identity:
            raise ValueError(f"Inline MaterialX needs a nodedef info:id: {path}")
        inputs={str(p.GetBaseName()):resolve(p.GetAttr()) for p in sorted(shader.GetInputs(),key=lambda p:str(p.GetBaseName()))
                if not str(p.GetBaseName()).startswith("hdue:")}
        nodes[path]=dict(id=str(identity),inputs=inputs)
        visiting.remove(path)
    output=material.GetSurfaceOutput("mtlx")
    if not output:
        raise ValueError(f"No MaterialX surface: {material.GetPath()}")
    terminal=resolve(output.GetAttr())
    if "node" not in terminal:
        raise ValueError(f"MaterialX surface has no shader: {material.GetPath()}")
    return dict(material=str(material.GetPath()),terminal=terminal,nodes=dict(sorted(nodes.items())))


def graph_signature(graph, mutable=()):
    data=json.loads(json.dumps(graph))
    for node in data["nodes"].values():
        for item in node["inputs"].values():
            if item.get("attribute") in mutable:
                item["value"]="<editable>"
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def rebase_graph(graph, old_root, new_root):
    """Rebase USD property identities after reference composition, not asset filenames."""
    data=json.loads(json.dumps(graph))
    def location(path):
        return new_root+path[len(old_root):] if path==old_root or path.startswith(old_root+'/') or path.startswith(old_root+'.') else path
    def port(item):
        for key in ('node','attribute'):
            if key in item: item[key]=location(item[key])
        item['trace']=[location(p) for p in item.get('trace',[])]
        return item
    data['material']=location(data['material'])
    data['terminal']=port(data['terminal'])
    data['nodes']={location(path):dict(node,inputs={n:port(v) for n,v in node['inputs'].items()}) for path,node in data['nodes'].items()}
    return data
