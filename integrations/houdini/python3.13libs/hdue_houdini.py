"""Houdini 22.0.466 Solaris host. All UE operations belong to the UI thread."""
import atexit
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

import hou
from pxr import Gf, Sdf, Usd, UsdGeom, UsdRender

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
PLUGIN='HdUEHoudiniRendererPlugin'
LABEL='Unreal Engine (hdUE)'
_host=None
_failure=None
_catalog_cache={}

def _report(message):
    print('hdUE Houdini: '+message,flush=True)
    directory=os.environ.get('HDUE_HOUDINI_LOG_DIR')
    if directory:
        path=Path(directory); path.mkdir(parents=True,exist_ok=True)
        with (path/'integration.log').open('a',encoding='utf-8') as log:
            log.write(time.strftime('%H:%M:%S ')+message+'\n')

def runtime_config():
    path=Path(os.environ['HDUE_RUNTIME_CONFIG']).resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('version')!=1: raise ValueError('Unsupported hdUE runtime config')
    for key in ('backend_dll','runtime_project','asset_catalog','materialx_library'):
        if key in config:
            value=Path(config[key]); config[key]=str(value if value.is_absolute() else (path.parent/value).resolve())
    return config

class Host:
    def __init__(self):
        from PySide6 import QtCore, QtWidgets
        from hdue_runtime import Runtime
        from hdue_viewport import Viewport
        if hou.applicationVersion()!=(22,0,466): raise RuntimeError('This package requires Houdini 22.0.466')
        self.owner=threading.get_ident()
        self.log_dir=Path(os.environ.get('HDUE_HOUDINI_LOG_DIR',str(ROOT/'out/houdini-22.0.466/session')))
        self.log_dir.mkdir(parents=True,exist_ok=True)
        config=runtime_config()
        os.environ['HDUE_BACKEND_DLL']=config['backend_dll']
        hou.putenv('HDUE_BACKEND_DLL',config['backend_dll'])
        self.runtime=Runtime(config['backend_dll'],config['runtime_project'],self.log_dir/'ue.log')
        self.api=Viewport(self.runtime,os.environ.get('HDUE_HOUDINI_PLUGIN',str(ROOT/'build/houdini-22.0.466/Release/hdUE_Houdini22.dll')))
        from hdue_materialx import PreparedMaterials
        self.prepared_materials=PreparedMaterials()
        self.material_error=None
        self.last_materials=0.
        self.viewer=None
        self.last_options=None
        self.closed=False
        self.last_state=time.monotonic()
        self.last_textures=0.
        self.texture_error=None
        self.running=True
        # Schedule after each completed Tick. A permanent due timer can starve
        # LOP cooks, while Houdini idle callbacks throttle UE to about 10 Hz.
        self.timer=QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.close)
        atexit.register(self.close)
        self.write_state()

    def write_state(self,error=None):
        data=dict(houdini=hou.applicationVersionString(),usd=Usd.GetVersion(),pid=os.getpid(),
                  owner=self.owner,ready=self.runtime.ready,closed=self.closed,
                  ticks=self.runtime.ticks,ue_initializations=self.runtime.initializations,
                  runtime_state=self.runtime.status().state,error=error,texture_error=self.texture_error,material_error=self.material_error)
        (self.log_dir/'runtime-state.json').write_text(json.dumps(data,indent=2),encoding='utf-8')

    def tick(self):
        global _failure
        if self.closed or not self.running: return
        try:
            state=self.api.status()
            node=current_settings_node()
            if node:
                if time.monotonic()-self.last_textures>.25:
                    self.last_textures=time.monotonic()
                    from hdue_op_textures import cache,refresh
                    bridge=node.node('asset_bridge')
                    if bridge and cache().dirty(bridge):
                        try:
                            refresh(bridge)
                            error='; '.join(bridge.errors()) or None
                        except (hou.Error,ValueError,RuntimeError) as failure:
                            error=str(failure)
                        if error and error!=self.texture_error: _report(error)
                        self.texture_error=error
                        scene_viewer().curViewport().draw()
                if state['active']:
                    stage=scene_viewer().stage()
                    if stage:
                        if state['session'] and time.monotonic()-self.last_materials>.25:
                            self.last_materials=time.monotonic()
                            try:
                                changed=self.prepared_materials.sync(stage,Usd.TimeCode(hou.frame()),node.evalParm('hdue_materialx_library'),state['session'],self.api)
                                if changed: scene_viewer().curViewport().draw()
                                self.material_error=None
                            except (ValueError,OSError,hou.Error) as error:
                                if str(error)!=self.material_error: _report(str(error))
                                self.material_error=str(error)
                        options=(state['session'],node.evalParm('hdue_mode')=='pathtraced',node.evalParm('hdue_samples'),
                                 UsdGeom.GetStageMetersPerUnit(stage),UsdGeom.GetStageUpAxis(stage)=='Z',hou.frame()/hou.fps())
                        if options!=self.last_options and self.api.options(*options[1:]):
                            self.last_options=options; scene_viewer().curViewport().draw()
            self.runtime.tick()
            if self.viewer:
                if state['error']: self.viewer.status.setText(state['error'])
                elif not state['active']: self.viewer.status.setText('Select Unreal Engine (hdUE) in Solaris to resume')
            if self.viewer and self.viewer.isVisible() and state['active'] and state['frame']['frame_id']:
                key=(state['session'],state['generation'],state['frame']['frame_id'])
                if key!=self.viewer.last_frame:
                    data=self.api.pixels(state,require_converged=False)
                    if data: self.viewer.present(state,*data)
            if time.monotonic()-self.last_state>2:
                self.last_state=time.monotonic(); self.write_state()
        except BaseException:
            _failure=traceback.format_exc()
            self.running=False; self.timer.stop()
            if self.viewer: self.viewer.status.setText(_failure.splitlines()[-1])
            self.write_state(_failure); _report(_failure)
        finally:
            if self.running and not self.closed: self.timer.start(8)

    def close(self):
        if self.closed: return
        self.running=False; self.timer.stop()
        # destroyifdeactivated removes Hydra sessions before UE shutdown.
        try:
            for viewer in hou.ui.paneTabs():
                if isinstance(viewer,hou.SceneViewer) and viewer.currentHydraRenderer() in (LABEL,PLUGIN):
                    viewer.setHydraRenderer('Houdini VK')
        except BaseException: _report(traceback.format_exc())
        if self.viewer: self.viewer.hide()
        self.runtime.close()
        from hdue_op_textures import close
        close()
        self.closed=True; self.write_state()

def start():
    global _host,_failure
    if _host: return _host
    try:
        _host=Host()
        _report('UE 5.8.4 ready on the Houdini UI thread')
        input_usd=os.environ.get('HDUE_HOUDINI_INPUT')
        if input_usd: open_usd(input_usd)
        test=os.environ.get('HDUE_HOUDINI_TEST')
        if test:
            if test=='lighting':
                import hdue_lighting_test
                hdue_lighting_test.start(_host)
            elif test=='materials':
                import hdue_materialx_test
                hdue_materialx_test.start(_host)
            elif test=='optextures':
                import hdue_op_texture_test
                hdue_op_texture_test.start(_host)
            elif test=='viewport':
                import hdue_solaris_probe
                hdue_solaris_probe.start(_host)
            else:
                import hdue_houdini_test
                hdue_houdini_test.start(_host)
    except BaseException:
        _failure=traceback.format_exc(); _report(_failure)
        path=Path(os.environ.get('HDUE_HOUDINI_LOG_DIR',str(ROOT/'out/houdini-22.0.466/session')))
        path.mkdir(parents=True,exist_ok=True)
        (path/'startup-error.txt').write_text(_failure,encoding='utf-8')
    return _host

def scene_viewer():
    viewer=hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if viewer is None: raise RuntimeError('Open a Scene Viewer pane for Solaris')
    return viewer

def activate():
    if not _host: start()
    if not _host or _failure: raise RuntimeError(_failure or 'UE not ready')
    viewer=scene_viewer()
    names=viewer.hydraRenderers()
    _report('Available Solaris renderers: '+repr(names))
    viewer.setHydraRenderer(LABEL if LABEL in names else PLUGIN)
    if os.environ.get('HDUE_HOUDINI_IMAGE_VIEWER')=='1': show_viewer()
    return viewer

def show_viewer():
    if not _host: start()
    if not _host or _failure: raise RuntimeError(_failure or 'UE not ready')
    if _host.viewer is None:
        from hdue_viewer import Viewer
        _host.viewer=Viewer(_host)
    _host.viewer.show()
    return _host.viewer

def current_settings_node():
    viewer=hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if viewer is None: return None
    network=viewer.pwd()
    node=network.displayNode() if isinstance(network,hou.LopNetwork) else viewer.currentNode()
    while node and not node.parm('hdue_mode'):
        inputs=node.inputs(); node=inputs[0] if inputs else None
    return node

def settings_node(input_node,metadata=None):
    from hdue_op_textures import prepare_stage
    prepare_stage(input_node.stage())
    if metadata is None:
        stage=input_node.stage()
        metadata={'metersperunit':UsdGeom.GetStageMetersPerUnit(stage),'upaxis':str(UsdGeom.GetStageUpAxis(stage))}
    node=input_node.parent().createNode('subnet','hdUE_settings')
    node.setInput(0,input_node)
    group=node.parmTemplateGroup()
    group.append(hou.StringParmTemplate('hdue_mode','Render mode',1,default_value=('realtime',),
        menu_items=('realtime','pathtraced'),menu_labels=('Lumen','Path Tracer')))
    group.append(hou.IntParmTemplate('hdue_samples','Path Tracer samples',1,default_value=(64,),min=1,max=4096))
    group.append(hou.StringParmTemplate('hdue_materialx_library','Cooked MaterialX library',1,
        default_value=(os.environ.get('HDUE_MATERIALX_LIBRARY') or runtime_config().get('materialx_library',''),),
        string_type=hou.stringParmType.FileReference))
    group.append(hou.StringParmTemplate('hdue_source_axis','Input up axis',1,default_value=(str(metadata['upaxis']).upper(),),menu_items=('Y','Z'),menu_labels=('Y','Z')))
    group.append(hou.FloatParmTemplate('hdue_source_units','Input meters per unit',1,default_value=(metadata['metersperunit'],),min=1e-9))
    node.setParmTemplateGroup(group)
    normalize=node.createNode('pythonscript','normalize_to_solaris')
    normalize.setInput(0,node.indirectInputs()[0])
    channels=normalize.parmTemplateGroup()
    channels.append(hou.StringParmTemplate('hdue_source_axis','Input up axis',1))
    channels.append(hou.FloatParmTemplate('hdue_source_units','Input meters per unit',1))
    normalize.setParmTemplateGroup(channels)
    normalize.parm('hdue_source_axis').setExpression('chs("../hdue_source_axis")',hou.exprLanguage.Hscript)
    normalize.parm('hdue_source_units').setExpression('ch("../hdue_source_units")',hou.exprLanguage.Hscript)
    normalize.parm('python').set('import hdue_houdini\nhdue_houdini.normalize(hou.pwd())')
    layer=node.createNode('configurelayer','stage_metadata')
    layer.setInput(0,normalize)
    layer.parm('setrendersettings').set(1); layer.parm('rendersettings').set('/Render/hdUE')
    for name,value in {'metersperunit':1.,'upaxis':'y'}.items():
        layer.parm('set'+name).set(1); layer.parm(name).set(value)
    bridge=node.createNode('pythonscript','asset_bridge')
    bridge.setInput(0,layer)
    channels=bridge.parmTemplateGroup()
    channels.append(hou.StringParmTemplate('hdue_mode','Render mode',1))
    channels.append(hou.IntParmTemplate('hdue_samples','Samples',1))
    bridge.setParmTemplateGroup(channels)
    bridge.parm('hdue_mode').setExpression('chs("../hdue_mode")',hou.exprLanguage.Hscript)
    bridge.parm('hdue_samples').setExpression('ch("../hdue_samples")',hou.exprLanguage.Hscript)
    bridge.parm('python').set('import hdue_houdini\nhdue_houdini.cook(hou.pwd())')
    node.node('output0').setInput(0,bridge)
    node.layoutChildren()
    return node

def normalize(node):
    from scene_space import normalize_stage
    stage=node.editableStage()
    # Hydra instancing is not implemented. Expand references only in this
    # generated layer so subsets and shaders inside instances remain editable.
    while True:
        instances=[prim for prim in stage.Traverse() if prim.IsInstance()]
        if not instances: break
        for prim in instances: prim.SetInstanceable(False)
    normalize_stage(stage,node.evalParm('hdue_source_axis'),node.evalParm('hdue_source_units'))

def cook(node):
    stage=node.editableStage()
    controls=node
    from hdue_op_textures import resolve_stage, preview_fallbacks
    resolve_stage(stage,node)
    preview_fallbacks(stage)
    # Author bounds meshes into this LOP's layer for Xform/Scope asset prims.
    # The delegate uses the primvar to instantiate the real cooked UE asset.
    from asset_proxy import author_proxy
    config=runtime_config()
    catalog_path=config.get('asset_catalog')
    catalog={}
    if catalog_path:
        key=(catalog_path,Path(catalog_path).stat().st_mtime_ns)
        if key not in _catalog_cache:
            _catalog_cache.clear()
            _catalog_cache[key]=json.loads(Path(catalog_path).read_text(encoding='utf-8-sig'))['assets']
        catalog=_catalog_cache[key]
    # A downstream edit can clear an asset reference from an already prepared
    # stage. Mask only generated proxies, and restore the original mesh purpose.
    for prim in list(stage.Traverse()):
        generated=prim.GetAttribute('hdue:generatedAssetProxy')
        if not generated or generated.Get()!=True: continue
        parent=prim.GetParent(); reference=parent.GetAttribute('hdue:asset')
        if reference and reference.Get(): continue
        prim.SetActive(False)
        original=parent.GetAttribute('hdue:originalPurpose')
        if original and parent.IsA(UsdGeom.Mesh) and UsdGeom.Mesh(parent).GetPointsAttr().Get():
            UsdGeom.Imageable(parent).CreatePurposeAttr(original.Get())
    references=[]
    for prim in stage.Traverse():
        attr=prim.GetAttribute('hdue:asset')
        asset=attr.Get() if attr else None
        if asset:
            if isinstance(asset,Sdf.AssetPath): asset=asset.path
            if not isinstance(asset,str): raise ValueError('hdue:asset requires a UE object path string: '+str(prim.GetPath()))
            references.append((str(prim.GetPath()),asset,prim.IsA(UsdGeom.Mesh)))
    for path,asset,is_mesh in references:
        prim=stage.GetPrimAtPath(path)
        # A child proxy also supports native references authored directly on
        # Mesh prims. The original mesh remains available as a guide.
        if is_mesh:
            imageable=UsdGeom.Imageable(prim)
            if not prim.GetAttribute('hdue:originalPurpose'):
                prim.CreateAttribute('hdue:originalPurpose',Sdf.ValueTypeNames.Token,custom=True).Set(imageable.GetPurposeAttr().Get())
            imageable.CreatePurposeAttr(UsdGeom.Tokens.guide)
        proxy=author_proxy(stage,path,asset,catalog,100.,'Y',False)
        proxy.GetPrim().SetActive(True)
        proxy.CreatePurposeAttr(UsdGeom.Tokens.render)
        correction=prim.GetAttribute('hdue:assetSpace')
        matrix=correction.Get() if correction else Gf.Matrix4d(1)
        xform=UsdGeom.Xformable(proxy)
        ops=xform.GetOrderedXformOps()
        op=next((op for op in ops if str(op.GetOpName())=='xformOp:transform:hdueAssetSpace'),None)
        if op is None: op=xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'hdueAssetSpace')
        xform.SetXformOpOrder([op]); op.Set(matrix)
        for package in catalog.get(asset,{}).get('unavailable_script_packages',[]):
            _report(f'{path}: unavailable source script package {package}; its actors were omitted by cook')
    settings=UsdRender.Settings.Define(stage,'/Render/hdUE')
    color=UsdRender.Var.Define(stage,'/Render/Vars/color')
    color.CreateDataTypeAttr('color4f'); color.CreateSourceNameAttr('color'); color.CreateSourceTypeAttr('raw')
    product=UsdRender.Product.Define(stage,'/Render/hdUE_product')
    product.CreateProductNameAttr('hdUE'); product.CreateProductTypeAttr('raster')
    product.CreateOrderedVarsRel().SetTargets([color.GetPath()])
    settings.CreateProductsRel().SetTargets([product.GetPath()])
    for name,type_,value in (('renderMode',Sdf.ValueTypeNames.String,controls.evalParm('hdue_mode')),
                              ('samplesPerPixel',Sdf.ValueTypeNames.Int,controls.evalParm('hdue_samples')),
                              ('lumenEnabled',Sdf.ValueTypeNames.Bool,True),
                              ('autoViewerLight',Sdf.ValueTypeNames.Bool,True),
                              ('upAxis',Sdf.ValueTypeNames.String,str(UsdGeom.GetStageUpAxis(stage)))):
        settings.GetPrim().CreateAttribute('hdUE:'+name,type_,custom=True).Set(value)

def open_usd(path):
    stage=hou.node('/stage')
    if stage is None: raise RuntimeError('Solaris /stage network is unavailable')
    source=stage.createNode('sublayer','hdUE_source')
    source.parm('filepath1').set(str(Path(path).resolve()))
    file_stage=Usd.Stage.Open(str(Path(path).resolve()),Usd.Stage.LoadNone)
    node=settings_node(source,{'metersperunit':UsdGeom.GetStageMetersPerUnit(file_stage),
                               'upaxis':str(UsdGeom.GetStageUpAxis(file_stage)).lower()})
    node.setDisplayFlag(True); node.setCurrent(True,clear_all_selected=True)
    stage.layoutChildren()
    viewer=scene_viewer(); viewer.setPwd(stage)
    _report('USD loaded at '+node.path())
    activate()
    from PySide6 import QtCore
    import hdefereval
    QtCore.QTimer.singleShot(500,lambda:hdefereval.execute_deferred(viewer.curViewport().frameAll))
    return node

def set_mode(mode):
    if mode not in ('realtime','pathtraced'): raise ValueError(mode)
    node=current_settings_node()
    if not node: raise RuntimeError('Select the hdUE settings LOP')
    node.parm('hdue_mode').set(mode)
    node.cook(force=True)
    scene_viewer().curViewport().draw()

