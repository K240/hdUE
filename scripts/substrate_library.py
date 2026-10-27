"""Bind checked Substrate Builder graphs to prepared native materials."""
import json
from pathlib import Path
from pxr import Gf, UsdShade, UsdGeom
from substrate_graph import read,signature,NODES,live_parameters,COMPILER_VERSION
from surface_effects import validate_mask


class NeedsPreparation(ValueError):
    """The graph is valid but has no matching compiled material."""


def graph_base(graph, entries):
    nodes=[n for n in graph['nodes'].values() if n['type']=='materialx']
    if not nodes: return None
    target=nodes[0]['properties']['material']
    if target not in entries: raise ValueError('Prepare and select the MaterialX library for Substrate input: '+target)
    return entries[target]


class SubstrateLibrary:
    def __init__(self,file=None):
        data=json.loads(Path(file).read_text(encoding='utf-8-sig')) if file else dict(version=1,materials=[])
        if data.get('version')!=1: raise ValueError('Unsupported Substrate Builder library version')
        if data.get('materials') and data.get('compiler')!=COMPILER_VERSION:
            raise ValueError('Substrate library requires the automatic-parameter compiler; prepare/cook/stage it again')
        from substrate_textures import validate_dependencies
        validate_dependencies(data)
        self.materials=data['materials']; self.owned=set(); self.diagnostics=[]

    def sync(self,stage,time,base_entries,settings):
        assets=dict(settings.get('nativeMaterialAssets',{})); overrides=dict(settings.get('nativeMaterialParameters',{}))
        for path in self.owned: assets.pop(path,None); overrides.pop(path,None)
        bound={}; owned=set()
        for prim in stage.Traverse():
            if not prim.IsA(UsdShade.Material) or not prim.GetAttribute('hdue:substrate:version'): continue
            material=UsdShade.Material(prim); graph=read(material,time); key=signature(graph)
            base=graph_base(graph,base_entries); identity=base['_hdue_identity'] if base else ''
            candidates=[p for p in self.materials if p['signature']==key and p.get('base_identity','')==identity]
            if not candidates: raise NeedsPreparation(str(prim.GetPath())+': Substrate graph changed or is unprepared; prepare/cook/stage it again')
            entry=sorted(candidates,key=lambda p:p['asset'])[0]; path=str(prim.GetPath()); owned.add(path)
            assets[path]=entry['asset']
            base_paths=[n['properties']['material'] for n in graph['nodes'].values() if n['type']=='materialx']
            fields=dict(overrides.get(base_paths[0],{})) if base_paths else {}
            for node in graph['nodes'].values():
                if node['type'] in ('scalar','color'):
                    props=node['properties']; value=props.get('value',NODES[node['type']]['properties']['value'][1])
                    name=props['parameter_name']
                    if name in fields: raise ValueError(path+': public Substrate parameter collides with MaterialX parameter: '+name)
                    fields[name]=Gf.Vec3f(*value) if node['type']=='color' else float(value)
            for parameter in live_parameters(graph):
                value=parameter['value']; kind=parameter['type']; name=parameter['name']
                if name in fields: raise ValueError(path+': generated Substrate parameter name collision: '+name)
                fields[name]=float(value) if kind=='float' else Gf.Vec4f(*(list(value)+[0.]*(4-len(value))))
            overrides[path]=fields
            masks=[n for n in graph['nodes'].values() if n['type']=='primvar']
            bound[path]=dict(entry=entry,mask=masks[0]['properties'].get('primvar','mask') if masks else '')
        effects=dict(settings.get('surfaceEffects',{})); diagnostics=[]
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh): continue
            targets=[prim]+[s.GetPrim() for s in UsdShade.MaterialBindingAPI(prim).GetMaterialBindSubsets()]
            for target in targets:
                material,_=UsdShade.MaterialBindingAPI(target).ComputeBoundMaterial()
                data=bound.get(str(material.GetPath())) if material else None
                if not data: continue
                validate_mask(UsdGeom.Mesh(prim),data['mask'],time)
                path=str(target.GetPath()); entry=data['entry']
                effects[path]=dict(asset=entry['asset'],previewAsset=entry['preview_asset'],maskPrimvar=data['mask'],amount=1.,roughness=0.,graph=True)
                diagnostics.append(dict(path=path,material=str(material.GetPath()),asset=entry['asset'],mask=data['mask']))
        settings.update(nativeMaterialAssets=assets,nativeMaterialParameters=overrides,surfaceEffects=effects)
        self.owned=owned; self.diagnostics=diagnostics
