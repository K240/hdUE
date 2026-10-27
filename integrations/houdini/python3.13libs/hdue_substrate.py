"""Substrate authoring in native Houdini VOP networks; no VEX evaluation."""
import json
from pathlib import Path
import hou
from pxr import Sdf

ROOT=Path(__file__).resolve().parents[3]
import sys
if str(ROOT/'scripts') not in sys.path: sys.path.insert(0,str(ROOT/'scripts'))
from substrate_graph import NODES,validate,author,outputs,ERROR_ATTRIBUTE

PREFIX='hdue::substrate_'
HDA_VERSION='1.3'
REVISION_PARM='hdue_graph_revision'


def _revision_parameter():
    parm=hou.IntParmTemplate(REVISION_PARM,'Graph revision',1,default_value=(0,))
    parm.hide(True)
    return parm


def type_name(kind): return PREFIX+kind+'::'+HDA_VERSION


def _connector(network,name,kind,value_type,default=None):
    node=network.createNode('subnetconnector',name)
    node.parm('connectorkind').set(kind); node.parm('parmname').set(name); node.parm('parmlabel').set(name.replace('_',' ').title())
    node.parm('parmtype').set({'surface':'bsdf','color3':'color','color4':'coloralpha','vector2':'float2'}.get(value_type,value_type))
    if kind=='input' and default is not None:
        if value_type=='float': node.parm('floatdef').set(default)
        elif value_type=='color3': node.parmTuple('colordef').set(default)
        elif value_type=='vector2': node.parmTuple('float2def').set(default)
        elif value_type=='color4': node.parmTuple('color4def').set(default)
    return node


def _parameter(name,kind,default):
    label=name.replace('_',' ').title()
    if kind=='string': return hou.StringParmTemplate(name,label,1,default_value=(default,))
    if kind=='asset': return hou.StringParmTemplate(name,label,1,default_value=(default,),string_type=hou.stringParmType.FileReference)
    if kind=='bool': return hou.ToggleParmTemplate(name,label,default_value=default)
    if kind=='color3': return hou.FloatParmTemplate(name,label,3,default_value=default,naming_scheme=hou.parmNamingScheme.RGBA,
                                                    look=hou.parmLook.ColorSquare)
    if kind in ('vector2','color4'): return hou.FloatParmTemplate(name,label,2 if kind=='vector2' else 4,default_value=default)
    return hou.FloatParmTemplate(name,label,1,default_value=(default,))


def install(file=None):
    """Build portable HDA definitions from source in an ignored output folder."""
    category=hou.vopNodeTypeCategory()
    expected=[type_name(k) for k in [*NODES,'builder']]
    if all(hou.nodeType(category,n) for n in expected): return
    target=Path(file) if file else ROOT/'out/substrate-builder/hdue_substrate.hda'
    target=target.resolve(); target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_file():
        hou.hda.installFile(str(target))
        if all(hou.nodeType(category,n) for n in expected): return
    # Generate in a private temporary subnet and remove only that node.
    scratch=hou.node('/mat').createNode('subnet','hdue_definition_build')
    try:
        for kind,definition in NODES.items():
            if hou.nodeType(category,type_name(kind)): continue
            node=scratch.createNode('subnet',kind)
            for name,(value_type,default) in definition['inputs'].items(): _connector(node,name,'input',value_type,default)
            for port,value_type in outputs(definition).items(): _connector(node,port,'output',value_type)
            group=node.parmTemplateGroup()
            for source in ('inputs','properties'):
                for name,(value_type,default) in definition[source].items():
                    if default is not None:
                        parameter=_parameter(name,value_type,default)
                        menus={'color_space':('srgb','raw'),'address':('wrap','clamp','mirror'),'channel':('r','g','b','a')}
                        if name in menus:
                            parameter.setMenuItems(menus[name]); parameter.setMenuLabels(tuple(s.upper() for s in menus[name]))
                        group.append(parameter)
            node.setParmTemplateGroup(group)
            for source in ('inputs','properties'):
                for name,(value_type,default) in definition[source].items():
                    if default is None: continue
                    if value_type in ('color3','vector2','color4'): node.parmTuple(name).set(default)
                    else: node.parm(name).set(default)
            asset=node.createDigitalAsset(name=type_name(kind),hda_file_name=str(target),description=definition['label'],
                                           min_num_inputs=0,max_num_inputs=len(definition['inputs']))
            # VOP asset creation generates a DialogScript from connectors and
            # resets numeric defaults. Apply the typed parameter defaults after
            # creating the definition so new instances and Reset to Default agree.
            asset.type().definition().setParmTemplateGroup(group)
            asset.type().definition().addSection('HDUE.Substrate.json',json.dumps(dict(version=1,operation=kind)))
            asset.setMaterialFlag(False); asset.destroy()
        if not hou.nodeType(category,type_name('builder')):
            node=scratch.createNode('subnet','builder')
            _connector(node,'surface','output','surface')
            asset=node.createDigitalAsset(name=type_name('builder'),hda_file_name=str(target),description='hdUE Substrate Builder',
                                           min_num_inputs=0,max_num_inputs=0)
            asset.type().definition().addSection('HDUE.Substrate.json',json.dumps(dict(version=1,operation='builder')))
            asset.setMaterialFlag(False); asset.destroy()
    finally: scratch.destroy()
    hou.hda.installFile(str(target))


def create(parent=None,preset='slab',material='/Looks/Base'):
    install()
    parent=parent or hou.node('/mat')
    with hou.undos.group('Create Substrate Builder'):
        network=parent.createNode(type_name('builder'),'substrate_builder')
        network.allowEditingOfContents(); network.setMaterialFlag(False)
        output=next(n for n in network.children() if n.type().name()=='subnetconnector')
        if preset=='slab':
            slab=network.createNode(type_name('slab'),'slab'); output.setInput(0,slab)
        elif preset=='wet':
            base=network.createNode(type_name('materialx'),'base'); base.parm('material').set(material)
            water=network.createNode(type_name('slab'),'water')
            water.parmTuple('diffuse_albedo').set((0,0,0)); f0=((1.33-1)/(1.33+1))**2
            water.parmTuple('f0').set((f0,f0,f0)); water.parmTuple('mfp').set((1e6,1e6,1e6)); water.parm('subsurface').set('simple_volume')
            rough=network.createNode(type_name('scalar'),'roughness'); rough.parm('parameter_name').set('WaterRoughness'); rough.parm('value').set(.08)
            water.setInput(2,rough)
            amount=network.createNode(type_name('scalar'),'amount'); amount.parm('parameter_name').set('Coverage'); amount.parm('value').set(1)
            saturate=network.createNode(type_name('saturate'),'clamp_amount'); saturate.setInput(0,amount)
            mask=network.createNode(type_name('primvar'),'mask'); mask.parm('primvar').set('wetness')
            multiply=network.createNode(type_name('multiply'),'coverage'); multiply.setInput(0,saturate); multiply.setInput(1,mask)
            weight=network.createNode(type_name('weight'),'weight'); weight.setInput(0,water); weight.setInput(1,multiply)
            layer=network.createNode(type_name('vertical'),'layer'); layer.setInput(0,weight); layer.setInput(1,base)
            layer.parm('thickness').set(.01)
            output.setInput(0,layer)
        else:
            network.destroy(); raise ValueError('Unknown Substrate preset: '+preset)
        network.layoutChildren(); network.moveToGoodPosition(); network.setCurrent(True,clear_all_selected=True)
    return network


def graph(network):
    output_nodes=[n for n in network.children() if n.type().name()=='subnetconnector' and n.parm('connectorkind').evalAsString()=='output']
    if len(output_nodes)!=1 or not output_nodes[0].inputs() or not output_nodes[0].inputs()[0]:
        raise ValueError('Substrate Builder needs one connected surface output')
    nodes={}; active=set()
    def visit(node):
        name=node.name()
        if name in active: raise ValueError('Substrate VOP cycle at '+node.path())
        if name in nodes: return name
        definition=node.type().definition()
        section=definition.sections().get('HDUE.Substrate.json') if definition else None
        if not section: raise ValueError('Unsupported VOP in Substrate Builder: '+node.path())
        kind=json.loads(section.contents())['operation']
        if kind not in NODES: raise ValueError('Nested Substrate Builders are not supported yet')
        schema=NODES[kind]; result=dict(type=kind,inputs={},properties={}); active.add(name)
        upstream=node.inputs()
        connections={c.inputIndex():c for c in node.inputConnections()}
        for index,(port,(value_type,default)) in enumerate(schema['inputs'].items()):
            source=upstream[index] if index<len(upstream) else None
            if source:
                source_name=visit(source)
                connection=connections[index]; output_index=connection.outputIndex()
                source_definition=json.loads(source.type().definition().sections()['HDUE.Substrate.json'].contents())['operation']
                source_outputs=list(outputs(NODES[source_definition]))
                if output_index>=len(source_outputs): raise ValueError('Unsupported source output: '+source.path())
                result['inputs'][port]=dict(node=source_name)
                if source_outputs[output_index]!='out': result['inputs'][port]['output']=source_outputs[output_index]
            elif default is not None:
                result['inputs'][port]=list(node.evalParmTuple(port)) if value_type in ('color3','vector2','color4') else node.evalParm(port)
        for prop,(value_type,default) in schema['properties'].items():
            value=list(node.evalParmTuple(prop)) if value_type in ('color3','vector2','color4') else bool(node.evalParm(prop)) if value_type=='bool' else node.evalParm(prop)
            if value_type=='asset' and value and not value.startswith(('op:','opdef:')):
                value=str((Path(hou.hipFile.path()).parent/Path(value)).resolve())
            result['properties'][prop]=value
        nodes[name]=result; active.remove(name); return name
    root=visit(output_nodes[0].inputs()[0])
    return validate(dict(version=1,output=root,nodes=nodes))


def author_lop(network,material_path='/Looks/Substrate',input_node=None,prim_paths=()):
    if not Sdf.Path.IsValidPathString(material_path) or not Sdf.Path(material_path).IsAbsolutePath() or not Sdf.Path(material_path).IsPrimPath():
        raise ValueError('Expected an absolute USD material prim path')
    validate(graph(network))
    parent=input_node.parent() if input_node else hou.node('/stage')
    with hou.undos.group('Author Substrate material'):
        node=parent.createNode('pythonscript','hdUE_substrate_material')
        if input_node: node.setInput(0,input_node)
        group=node.parmTemplateGroup()
        group.append(hou.StringParmTemplate('hdue_builder','Substrate VOP network',1,default_value=(network.path(),),string_type=hou.stringParmType.NodeReference))
        group.append(hou.StringParmTemplate('hdue_material','USD material',1,default_value=(material_path,)))
        group.append(hou.StringParmTemplate('hdue_bindings','Bind prim paths',1,default_value=(' '.join(prim_paths),)))
        group.append(_revision_parameter())
        node.setParmTemplateGroup(group)
        node.parm('python').set('import hdue_substrate\nhdue_substrate.cook(hou.pwd())')
        node.setDisplayFlag(True); node.setCurrent(True,clear_all_selected=True); node.moveToGoodPosition()
    return node


def cook(node):
    from pxr import UsdShade
    network=hou.node(node.evalParm('hdue_builder'))
    if not network: raise ValueError('Substrate VOP network not found')
    # Evaluating the connected VOP parameters and wiring inside this Python LOP
    # records native Houdini parameter dependencies. Wiring notifications only
    # dirty a hidden revision parm; they never force a cook from an event handler.
    _watch_structure(network,node)
    node.evalParm(REVISION_PARM)
    stage=node.editableStage(); path=node.evalParm('hdue_material')
    try:
        current=graph(network)
        material=author(stage,path,current)
    except ValueError as error:
        # Incomplete wiring is an ordinary editing state. A failed Python LOP
        # can lose the stage and prevent the host from observing later repairs.
        existing=stage.GetPrimAtPath(path)
        if existing and existing.GetAttribute('hdue:substrate:version').Get()!=1: raise
        material=UsdShade.Material.Define(stage,path)
        material.GetPrim().CreateAttribute('hdue:substrate:version',Sdf.ValueTypeNames.Int).Set(1)
        material.GetPrim().CreateAttribute(ERROR_ATTRIBUTE,Sdf.ValueTypeNames.String).Set(str(error))
        return
    for path in node.evalParm('hdue_bindings').split():
        prim=stage.GetPrimAtPath(path)
        if not prim: raise ValueError('Substrate binding target not found: '+path)
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


_structure_watchers={}
def _watch_structure(network,target):
    identity=(network.sessionId(),target.sessionId())
    if identity in _structure_watchers: return
    if not target.parm(REVISION_PARM):
        with hou.undos.disabler(): target.addSpareParmTuple(_revision_parameter())
    events=(hou.nodeEventType.InputRewired,hou.nodeEventType.ChildCreated,hou.nodeEventType.ChildDeleted)
    watched={}; pending=[False]; alive=[True]
    def notify():
        pending[0]=False
        if not alive[0]: return
        try:
            register()
            with hou.undos.disabler():
                parm=target.parm(REVISION_PARM); parm.set((parm.eval()+1)%2000000000)
        except (hou.ObjectWasDeleted,hou.OperationFailed): pass
    def changed(**kwargs):
        if pending[0] or not alive[0]: return
        if hou.isUIAvailable():
            # InputRewired can fire before the native wire edit has committed.
            # Defer publication so a viewport cook cannot read the old topology
            # inside the event callback and then miss the completed disconnect.
            from PySide6 import QtCore
            pending[0]=True; QtCore.QTimer.singleShot(50,notify)
        else: notify()
    def register():
        sources=(network,*network.children()); current={s.sessionId() for s in sources}
        for key in set(watched)-current:
            source=watched.pop(key)
            try: source.removeEventCallback(events,changed)
            except hou.Error: pass
        for source in sources:
            if source.sessionId() in watched: continue
            source.addEventCallback(events,changed); watched[source.sessionId()]=source
    def cleanup(**kwargs):
        alive[0]=False
        for source in watched.values():
            try: source.removeEventCallback(events,changed)
            except hou.Error: pass
        for endpoint in (network,target):
            try: endpoint.removeEventCallback((hou.nodeEventType.BeingDeleted,),cleanup)
            except hou.Error: pass
        _structure_watchers.pop(identity,None)
    register()
    for endpoint in (network,target): endpoint.addEventCallback((hou.nodeEventType.BeingDeleted,),cleanup)
    _structure_watchers[identity]=changed


def publish(network=None):
    import hdue_houdini
    if network is None:
        def is_builder(node):
            definition=node.type().definition()
            section=definition.sections().get('HDUE.Substrate.json') if definition else None
            return section and json.loads(section.contents()).get('operation')=='builder'
        selected=[n for n in hou.selectedNodes() if is_builder(n)]
        if len(selected)!=1: raise ValueError('Select one Substrate Builder VOP network to author.')
        network=selected[0]
    viewer=hdue_houdini.scene_viewer(); parent=viewer.pwd()
    if not isinstance(parent,hou.LopNetwork): parent=hou.node('/stage')
    return author_lop(network,'/Looks/'+network.name(),parent.displayNode(),list(viewer.currentSceneGraphSelection()))
