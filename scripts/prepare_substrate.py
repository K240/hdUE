# /// script
# requires-python = ">=3.11"
# ///
"""Export typed USD Substrate graphs, then assemble editor preparation receipts."""
import argparse
import hashlib
import json
from pathlib import Path
from pxr import Usd,UsdShade
from substrate_graph import read,signature,validate,live_parameters,COMPILER_VERSION
from substrate_library import graph_base
from substrate_textures import prepare as prepare_textures


def collect(stage,time,entries):
    records=[]
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material) or not prim.GetAttribute('hdue:substrate:version'): continue
        graph=read(UsdShade.Material(prim),time); base=graph_base(graph,entries)
        records.append(dict(material=str(prim.GetPath()),graph=graph,
                            base_identity=base['_hdue_identity'] if base else '',base_asset=base['asset'] if base else ''))
    return records


def export(scene,library,output,destination,time):
    from prepare_renderer_settings import export as material_settings
    from materialx_controls import MaterialXLibrary
    from types import SimpleNamespace
    stage=Usd.Stage.Open(str(scene.resolve()))
    entries={}
    if library:
        # The same resolver/identity rules are used by both interactive hosts.
        class Signal:
            def connect(self,callback): pass
        class View:
            _renderer=object()
            def __init__(self): self.settings={}
            def GetRendererSetting(self,name): return self.settings.get(name)
            def SetRendererSetting(self,name,value): self.settings[name]=value
        view=View(); model=SimpleNamespace(stage=stage,currentFrame=time,signalStageReplaced=Signal(),signalPrimsChanged=Signal())
        prepared=MaterialXLibrary(library,SimpleNamespace(_dataModel=model,_stageView=view))
        if not prepared.sync(): raise ValueError(prepared.preparation_error)
        entries=prepared.bound_entries
    export_records(collect(stage,time,entries),output,destination)


def export_records(records,output,destination):
    output.mkdir(parents=True,exist_ok=False); manifests=[]; dependencies={}; texture_sets={}
    for record in records:
        graph=validate(record['graph']); key=signature(graph)
        textures,texture_dependencies,sets=prepare_textures(graph,output,destination)
        dependencies.update(texture_dependencies); texture_sets.update({s['pattern']:s for s in sets})
        identity=record.get('base_identity','')
        cache=hashlib.sha256((key+identity+json.dumps(textures,sort_keys=True)).encode()).hexdigest()[:16]
        package=destination.rstrip('/')+'/M_'+cache
        data=dict(version=1,material=record['material'],signature=key,base_identity=identity,
                  base_asset=record.get('base_asset',''),asset=package,graph=graph,textures=textures,
                  compiler=COMPILER_VERSION,parameters=live_parameters(graph))
        file=output/('M_'+cache+'.json'); file.write_text(json.dumps(data,indent=2),encoding='utf-8')
        masks=[(name,n) for name,n in graph['nodes'].items() if n['type']=='primvar']
        mask_id=masks[0][0] if masks else 'mask'
        color_id='_preview_color' if mask_id!='_preview_color' else '_preview_color2'
        unlit_id='_preview_surface' if mask_id!='_preview_surface' else '_preview_surface2'
        preview_graph=dict(version=1,output=unlit_id,nodes={
            mask_id:masks[0][1] if masks else dict(type='constant',properties={'value':1.}),
            color_id:dict(type='color_from_float',inputs={'value':{'node':mask_id}}),
            unlit_id:dict(type='unlit',inputs={'emission':{'node':color_id}})})
        validate(preview_graph)
        preview=output/('M_'+cache+'_preview.json')
        preview.write_text(json.dumps(dict(asset=package+'_Mask',graph=preview_graph,compiler=COMPILER_VERSION,
                                          parameters=live_parameters(preview_graph)),indent=2),encoding='utf-8')
        manifests.append(dict(file=str(file.resolve()),preview=str(preview.resolve()),material=record['material'],signature=key,base_identity=identity))
    if not manifests: raise ValueError('No Substrate Builder materials in the stage')
    (output/'export.json').write_text(json.dumps(manifests,indent=2),encoding='utf-8')
    (output/'textures.json').write_text(json.dumps(dict(dependencies=dependencies,texture_sets=list(texture_sets.values())),indent=2),encoding='utf-8')


def registry(folder):
    materials=[]
    for entry in json.loads((folder/'export.json').read_text()):
        file=Path(entry['file']); receipt=json.loads(file.with_suffix('.receipt.json').read_text(encoding='utf-8-sig'))
        preview=json.loads(Path(entry['preview']).with_suffix('.receipt.json').read_text(encoding='utf-8-sig'))
        if not receipt.get('native_substrate') or not preview.get('native_substrate'): raise ValueError('Native Substrate compilation failed')
        if receipt.get('compiler')!=COMPILER_VERSION or preview.get('compiler')!=COMPILER_VERSION:
            raise ValueError('Substrate editor/compiler versions do not match; rebuild and prepare again')
        materials.append(dict(material=entry['material'],signature=entry['signature'],base_identity=entry['base_identity'],
                              asset=receipt['asset'],preview_asset=preview['asset']))
    textures=json.loads((folder/'textures.json').read_text(encoding='utf-8'))
    from substrate_textures import validate_dependencies
    validate_dependencies(textures)
    (folder/'library.json').write_text(json.dumps(dict(version=1,compiler=COMPILER_VERSION,materials=materials,**textures),indent=2),encoding='utf-8')


def publish(folder,runtime_project,destination):
    """Atomically add immutable loose cooked packages, without touching shaders/libraries already loaded."""
    import re
    import shutil
    import uuid
    from substrate_textures import validate_dependencies
    if not re.fullmatch(r'/Game/HdUE/Live/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+',destination): raise ValueError('Invalid live package destination')
    runtime_project=runtime_project.resolve()
    if not runtime_project.is_file(): raise ValueError('The active runtime project is missing')
    content=(runtime_project.parent/'Content').resolve(); relative=Path(destination.removeprefix('/Game/'))
    source=folder/'cooked/Windows/HdUEBackend/Content'/relative
    target=(content/relative).resolve()
    if not target.is_relative_to(content/'HdUE/Live') or not source.is_dir():
        raise ValueError('Cooked live package directory is missing or outside the live content root')
    library=json.loads((folder/'library.json').read_text(encoding='utf-8'))
    validate_dependencies(library)
    for entry in library['materials']:
        for field in ('asset','preview_asset'):
            package=entry[field].split('.')[0]
            if not package.startswith(destination+'/') or not (source/(package[len(destination)+1:]+'.uasset')).is_file():
                raise ValueError('The cooked material/preview package is missing: '+package)
    files=[file for file in source.rglob('*') if file.is_file()]
    if not files or any(file.suffix not in ('.uasset','.uexp','.ubulk','.uptnl') for file in files):
        raise ValueError('Unexpected payload in the live cooked package directory')
    if target.exists(): raise FileExistsError('Live package names must be immutable: '+str(target))
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.incoming-'+uuid.uuid4().hex)
    shutil.copytree(source,temporary)
    temporary.rename(target)
    (folder/'published.json').write_text(json.dumps(dict(version=1,runtime_project=str(runtime_project),destination=destination,
        files=len(files),library=str((folder/'library.json').resolve()),inline_shader_code=True),indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('export'); p.add_argument('scene',type=Path); p.add_argument('--library',type=Path); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--destination',default='/Game/HdUE/Substrate'); p.add_argument('--time',type=float,default=0)
    p=sub.add_parser('registry'); p.add_argument('folder',type=Path)
    p=sub.add_parser('export-job'); p.add_argument('job',type=Path); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--destination',required=True)
    p=sub.add_parser('publish'); p.add_argument('folder',type=Path); p.add_argument('--runtime-project',type=Path,required=True)
    p.add_argument('--destination',required=True)
    args=parser.parse_args()
    if args.mode=='registry': registry(args.folder)
    elif args.mode=='export-job':
        job=json.loads(args.job.read_text(encoding='utf-8'))
        if job.get('version')!=1: raise ValueError('Unsupported live preparation job')
        export_records(job['materials'],args.output,args.destination)
    elif args.mode=='publish': publish(args.folder,args.runtime_project,args.destination)
    else: export(args.scene,args.library,args.output,args.destination,Usd.TimeCode(args.time))
