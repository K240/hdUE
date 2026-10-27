"""Resolve USD image assets and snapshot file/UDIM inputs for Builder preparation."""
import hashlib
import json
from pathlib import Path
import shutil
from pxr import Sdf
from materialx_textures import udim_tiles,validate_texture_sets


def resolve_asset(attribute,value,time):
    if not isinstance(value,Sdf.AssetPath) or not value.path:
        raise ValueError(str(attribute.GetPath())+': expected a nonempty texture asset')
    raw=value.path
    if raw.startswith(('op:','opdef:')): return raw
    if value.resolvedPath: return str(Path(value.resolvedPath).resolve())
    if Path(raw).is_absolute(): return str(Path(raw).resolve())
    layer=next((s.layer for s in attribute.GetPropertyStack(time)
                if s.HasDefaultValue() or s.layer.ListTimeSamplesForPath(s.path)),None)
    if layer:
        anchored=Path(Sdf.ComputeAssetPathRelativeToLayer(layer,raw))
        if anchored.is_absolute(): return str(anchored.resolve())
        if layer.realPath: return str((Path(layer.realPath).parent/raw).resolve())
    raise ValueError(str(attribute.GetPath())+': cannot anchor texture to a filesystem USD layer')


def prepare(graph,folder,destination):
    textures={}; dependencies={}; sets={}
    for name,node in graph['nodes'].items():
        if node['type']!='texture': continue
        props=node['properties']; raw=props['file']
        if raw.startswith(('op:','opdef:')):
            raise ValueError(name+': direct Builder textures require exported image files; bake '+raw+' first')
        path=Path(raw)
        if not path.is_absolute(): raise ValueError(name+': texture path must be resolved before preparation')
        tiles=udim_tiles(raw) if '<UDIM>' in raw else {1001:str(path.resolve())}
        hashes={}
        for index,file in tiles.items():
            source=Path(file)
            if not source.is_file(): raise ValueError(name+': missing texture '+file)
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            dependencies[file]=digest; hashes[str(index)]=digest
        if '<UDIM>' in raw: sets[raw]=dict(pattern=raw,tiles={str(k):v for k,v in tiles.items()})
        srgb=props.get('color_space','srgb')=='srgb'; address=props.get('address','wrap')
        key=hashlib.sha256(json.dumps(dict(tiles=hashes,srgb=srgb,address=address),sort_keys=True).encode()).hexdigest()[:24]
        snapshot=folder/'textures'/key; snapshot.mkdir(parents=True,exist_ok=True)
        sources={}
        for index,file in tiles.items():
            # Isolate images from neighboring numbered files: only an explicit
            # <UDIM> token enables automatic blocked import in TextureFactory.
            target=snapshot/('source'+('.'+str(index) if len(tiles)>1 else '')+Path(file).suffix.lower())
            shutil.copyfile(file,target); sources[index]=str(target.resolve())
        textures[name]=dict(asset=destination.rstrip('/')+'/Textures/T_'+key,source=next(iter(sources.values())),
                            tiles=list(tiles),srgb=srgb,address=address)
    return textures,dependencies,list(sets.values())


def validate_dependencies(data):
    validate_texture_sets(data.get('texture_sets',[]))
    for file,expected in data.get('dependencies',{}).items():
        path=Path(file)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Substrate texture changed; prepare/cook/stage again: '+file)
