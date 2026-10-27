"""Versioned, typed Substrate graph shared by VOP, USD and UE preparation."""
import hashlib
import json
import math
import struct
from pxr import Sdf, UsdShade

# (type, default). None is an optional, unconnected shader input.
NODES = {
    'materialx': dict(label='MaterialX Surface', output='surface', inputs={}, properties={'material': ('string','/Looks/Base')}),
    'slab': dict(label='Substrate Slab', output='surface', inputs={
        'diffuse_albedo': ('color3',(.18,.18,.18)), 'f0': ('color3',(.04,.04,.04)),
        'roughness': ('float',.5), 'normal': ('color3',None), 'mfp': ('color3',(0.,0.,0.))},
        properties={'subsurface': ('string','none')}),
    'weight': dict(label='Substrate Weight', output='surface', inputs={'surface': ('surface',None),'weight': ('float',1.)}, properties={}),
    'vertical': dict(label='Substrate Vertical Layer', output='surface', inputs={
        'top': ('surface',None),'base': ('surface',None),'thickness': ('float',.01)}, properties={'parameter_blending': ('bool',False)}),
    'horizontal': dict(label='Substrate Horizontal Mix', output='surface', inputs={
        'background': ('surface',None),'foreground': ('surface',None),'mix': ('float',.5)}, properties={'parameter_blending': ('bool',False)}),
    'scalar': dict(label='Scalar Parameter', output='float', inputs={}, properties={'parameter_name': ('string','Amount'),'value': ('float',1.)}),
    'color': dict(label='Color Parameter', output='color3', inputs={}, properties={'parameter_name': ('string','Color'),'value': ('color3',(1.,1.,1.))}),
    'constant': dict(label='Float Constant', output='float', inputs={}, properties={'value': ('float',1.)}),
    'constant_color': dict(label='Color Constant', output='color3', inputs={}, properties={'value': ('color3',(1.,1.,1.))}),
    'multiply': dict(label='Float Multiply', output='float', inputs={'a': ('float',1.),'b': ('float',1.)}, properties={}),
    'saturate': dict(label='Float Saturate', output='float', inputs={'value': ('float',0.)}, properties={}),
    'primvar': dict(label='Float Mask Primvar [0..1]', output='float', inputs={}, properties={'primvar': ('string','mask'),'default': ('float',1.)}),
    'color_from_float': dict(label='Float to Color',output='color3',inputs={'value':('float',0.)},properties={}),
    'unlit': dict(label='Substrate Unlit',output='surface',inputs={'emission':('color3',(0.,0.,0.))},properties={}),
    'uv': dict(label='UV Coordinates',output='vector2',inputs={
        'scale':('vector2',(1.,1.)), 'offset':('vector2',(0.,0.))},properties={'index':('float',0.)}),
    'texture': dict(label='Texture Sample',output='color3',outputs={
        'out':'color3','rgba':'color4','r':'float','g':'float','b':'float','a':'float'},
        inputs={'uv':('vector2',None)},properties={
            'file':('asset',''), 'color_space':('string','srgb'), 'address':('string','wrap')}),
    'channel': dict(label='Extract Channel',output='float',inputs={'value':('color4',None)},properties={'channel':('string','r')}),
    'normalmap': dict(label='Normal Map',output='color3',inputs={
        'color':('color3',(.5,.5,1.)), 'strength':('float',1.)},properties={'flip_green':('bool',True)}),
}
USD_TYPES={'float':Sdf.ValueTypeNames.Float,'color3':Sdf.ValueTypeNames.Color3f,'surface':Sdf.ValueTypeNames.Token,
           'string':Sdf.ValueTypeNames.String,'bool':Sdf.ValueTypeNames.Bool,'asset':Sdf.ValueTypeNames.Asset,
           'vector2':Sdf.ValueTypeNames.Float2,'color4':Sdf.ValueTypeNames.Color4f}
COMPILER_VERSION=2
ERROR_ATTRIBUTE='hdue:substrate:authoringError'


def live_type(kind,category,port):
    value_type=NODES[kind][category][port][0]
    if category=='inputs' and value_type in ('float','color3','vector2','color4'): return value_type
    if category=='properties':
        if kind in ('constant','constant_color') and port=='value': return value_type
        if kind=='primvar' and port=='default': return 'float'
        if kind=='normalmap' and port=='flip_green': return 'float'
        if kind=='channel' and port=='channel': return 'color4'
    return None


def live_parameters(graph):
    """Stable reserved names shared by editor generation and runtime binding."""
    result=[]
    for node_name,node in validate(graph)['nodes'].items():
        for category in ('inputs','properties'):
            for port,(_,default) in NODES[node['type']][category].items():
                kind=live_type(node['type'],category,port)
                value=node.get(category,{}).get(port,default)
                if kind is None or value is None or isinstance(value,dict): continue
                if node['type']=='normalmap' and port=='flip_green': value=float(value)
                if node['type']=='channel' and port=='channel': value=[float(c==value) for c in 'rgba']
                key=node_name+'.'+category+'.'+port
                name='HdUE_Auto_'+hashlib.sha256(key.encode()).hexdigest()[:24]
                result.append(dict(key=key,name=name,type=kind,value=value))
    return result


def outputs(definition):
    return definition.get('outputs',{'out':definition['output']})


def _value(value, kind, where):
    if kind in ('string','asset'):
        if not isinstance(value,str): raise ValueError(where+': expected string')
    elif kind=='bool':
        if not isinstance(value,bool): raise ValueError(where+': expected bool')
    elif kind in ('float','color3','vector2','color4'):
        values=[value] if kind=='float' else value
        size={'color3':3,'vector2':2,'color4':4}.get(kind)
        if size and (not isinstance(values,(list,tuple)) or len(values)!=size): raise ValueError(where+': expected '+kind)
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values):
            raise ValueError(where+': expected finite numeric data')
        try:
            if not all(math.isfinite(struct.unpack('<f',struct.pack('<f',v))[0]) for v in values): raise ValueError(where+': float32 overflow')
        except OverflowError as error: raise ValueError(where+': float32 overflow') from error
    else: raise ValueError(where+': surface inputs require a connection')


def validate(graph):
    if graph.get('version')!=1 or not isinstance(graph.get('nodes'),dict) or not 0<len(graph['nodes'])<=256:
        raise ValueError('Unsupported or empty Substrate graph (maximum 256 nodes)')
    nodes=graph['nodes']; output=graph.get('output'); visited=set(); active=set(); parameters=set(); bases=[]; masks=[]
    def visit(name):
        if name in active: raise ValueError('Substrate graph cycle at '+name)
        if name in visited: return
        node=nodes.get(name)
        if not node or node.get('type') not in NODES: raise ValueError('Unknown Substrate node: '+str(name))
        if not Sdf.Path.IsValidIdentifier(name): raise ValueError('Invalid Substrate node identifier: '+name)
        definition=NODES[node['type']]; active.add(name)
        if set(node.get('inputs',{}))-set(definition['inputs']) or set(node.get('properties',{}))-set(definition['properties']):
            raise ValueError(name+': unknown input/property')
        for port,(kind,default) in definition['inputs'].items():
            value=node.get('inputs',{}).get(port,default)
            if isinstance(value,dict):
                if set(value)-{'node','output'} or 'node' not in value: raise ValueError(name+'.'+port+': invalid connection')
                visit(value['node'])
                if outputs(NODES[nodes[value['node']]['type']]).get(value.get('output','out'))!=kind:
                    raise ValueError(name+'.'+port+': connection type mismatch or unknown output')
            elif value is None:
                if kind=='surface' or node['type']=='channel': raise ValueError(name+'.'+port+': missing required connection')
            else: _value(value,kind,name+'.'+port)
        for prop,(kind,default) in definition['properties'].items():
            _value(node.get('properties',{}).get(prop,default),kind,name+'.'+prop)
        props=node.get('properties',{})
        if node['type'] in ('scalar','color'):
            public=props.get('parameter_name',definition['properties']['parameter_name'][1])
            if not public or public.lower().startswith('hdue_') or public.casefold() in parameters: raise ValueError(name+': duplicate, empty or reserved parameter name')
            parameters.add(public.casefold())
        if node['type']=='slab' and props.get('subsurface','none') not in ('none','simple_volume'):
            raise ValueError(name+': unsupported subsurface mode')
        if node['type']=='uv' and props.get('index',0)!=0:
            raise ValueError(name+': only UV0 (USD st) is transported by the current mesh bridge')
        if node['type']=='texture':
            if not props.get('file','').strip(): raise ValueError(name+': Texture Sample requires an image file')
            if props.get('color_space','srgb') not in ('srgb','raw'): raise ValueError(name+': expected srgb or raw color space')
            if props.get('address','wrap') not in ('wrap','clamp','mirror'): raise ValueError(name+': unsupported texture address mode')
        if node['type']=='channel' and props.get('channel','r') not in ('r','g','b','a'):
            raise ValueError(name+': expected r, g, b or a channel')
        if node['type']=='normalmap':
            source=node.get('inputs',{}).get('color')
            if isinstance(source,dict):
                upstream=nodes[source['node']]
                if upstream['type']=='texture' and upstream.get('properties',{}).get('color_space','srgb')!='raw':
                    raise ValueError(name+': Normal Map requires a raw Texture Sample, not sRGB')
        if node['type']=='materialx': bases.append(name)
        if node['type']=='primvar': masks.append(name)
        active.remove(name); visited.add(name)
    visit(output)
    if NODES[nodes[output]['type']]['output']!='surface': raise ValueError('Substrate output must be a surface')
    if set(nodes)!=visited: raise ValueError('Disconnected Substrate nodes: '+', '.join(sorted(set(nodes)-visited)))
    if len(bases)>1: raise ValueError('The initial compiler supports one MaterialX surface input per graph')
    if len(masks)>1: raise ValueError('The initial mask transport supports one Float Primvar node per graph')
    return graph


def signature(graph):
    graph=json.loads(json.dumps(validate(graph)))
    graph['compiler']=COMPILER_VERSION
    for node in graph['nodes'].values():
        schema=NODES[node['type']]
        for category in ('inputs','properties'):
            fields=node.setdefault(category,{})
            for name,(kind,default) in schema[category].items():
                if name not in fields and default is not None: fields[name]=default
                value=fields.get(name)
                dynamic=live_type(node['type'],category,name)
                if dynamic and value is not None and not isinstance(value,dict):
                    fields[name]={'parameter':dynamic}; continue
                if isinstance(value,dict) and value.get('output')=='out': value.pop('output')
                if value is not None and not isinstance(value,dict) and kind in ('float','color3','vector2','color4'):
                    f32=lambda v: struct.unpack('<f',struct.pack('<f',v))[0]
                    fields[name]=f32(value) if kind=='float' else [f32(v) for v in value]
        if node['type'] in ('scalar','color'): node.get('properties',{}).pop('value',None)
        if node['type']=='primvar': node.get('properties',{}).pop('primvar',None)
        if node['type']=='materialx': node.get('properties',{}).pop('material',None)
    return hashlib.sha256(json.dumps(graph,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def author(stage,path,graph):
    from pxr import Gf
    validate(graph)
    existing=stage.GetPrimAtPath(path)
    if existing and existing.GetAttribute('hdue:substrate:version').Get()!=1:
        raise ValueError('Refusing to replace a material not owned by Substrate Builder: '+path)
    if existing: stage.RemovePrim(path)
    material=UsdShade.Material.Define(stage,path)
    if material.GetPrim().GetAttribute(ERROR_ATTRIBUTE): material.GetPrim().GetAttribute(ERROR_ATTRIBUTE).Block()
    material.GetPrim().CreateAttribute('hdue:substrate:version',Sdf.ValueTypeNames.Int).Set(1)
    shaders={name:UsdShade.Shader.Define(stage,path+'/Graph/'+name) for name in graph['nodes']}
    for name,node in graph['nodes'].items():
        shader=shaders[name]; definition=NODES[node['type']]
        shader.CreateIdAttr('hdue:substrate:'+node['type'])
        for port,kind in outputs(definition).items(): shader.CreateOutput(port,USD_TYPES[kind])
        for category in ('inputs','properties'):
            for port,(kind,default) in definition[category].items():
                value=node.get(category,{}).get(port,default)
                if node['type']=='materialx' and port=='material':
                    shader.GetPrim().CreateRelationship('hdue:materialx').SetTargets([Sdf.Path(value)])
                    continue
                attribute=shader.CreateInput(port,USD_TYPES[kind])
                if isinstance(value,dict): attribute.ConnectToSource(shaders[value['node']].ConnectableAPI(),value.get('output','out'))
                else:
                    # Value edits and disconnects must override weaker source
                    # connections, including a referenced Builder material.
                    attribute.GetAttr().SetConnections([])
                    if value is None:
                        attribute.GetAttr().Block(); continue
                    constructors={'color3':Gf.Vec3f,'vector2':Gf.Vec2f,'color4':Gf.Vec4f}
                    attribute.Set(constructors[kind](*value) if kind in constructors else Sdf.AssetPath(value) if kind=='asset' else value)
    material.CreateSurfaceOutput('hdue').ConnectToSource(shaders[graph['output']].ConnectableAPI(),'out')
    preview=UsdShade.Shader.Define(stage,path+'/Preview'); preview.CreateIdAttr('UsdPreviewSurface')
    preview.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.18))
    preview.CreateOutput('surface',Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(),'surface')
    return material


def read(material,time):
    error=material.GetPrim().GetAttribute(ERROR_ATTRIBUTE)
    if error and error.Get(time): raise ValueError(str(material.GetPath())+': '+error.Get(time))
    if material.GetPrim().GetAttribute('hdue:substrate:version').Get()!=1: raise ValueError('Unsupported Substrate material version')
    terminal=material.GetSurfaceOutput('hdue').GetConnectedSource()
    if not terminal: raise ValueError(str(material.GetPath())+': missing hdue surface output')
    nodes={}
    def visit(shader):
        name=shader.GetPrim().GetName()
        if name in nodes: return name
        kind=str(shader.GetIdAttr().Get()).removeprefix('hdue:substrate:')
        if kind not in NODES: raise ValueError('Unsupported Substrate shader: '+str(shader.GetPath()))
        node=dict(type=kind,inputs={},properties={}); nodes[name]=node
        for category in ('inputs','properties'):
            for port,(value_type,default) in NODES[kind][category].items():
                if kind=='materialx' and port=='material':
                    targets=shader.GetPrim().GetRelationship('hdue:materialx').GetForwardedTargets()
                    if len(targets)!=1: raise ValueError('MaterialX input requires one material relationship target')
                    node[category][port]=str(targets[0]); continue
                attr=shader.GetInput(port)
                if not attr: continue
                if attr.GetTypeName()!=USD_TYPES[value_type]: raise ValueError(str(attr.GetAttr().GetPath())+': invalid graph input type')
                connection=attr.GetConnectedSource()
                if connection:
                    if category!='inputs': raise ValueError('Invalid property connection')
                    source=UsdShade.Shader(connection[0].GetPrim()); source_kind=str(source.GetIdAttr().Get()).removeprefix('hdue:substrate:')
                    if source_kind not in NODES or outputs(NODES[source_kind]).get(str(connection[1]))!=value_type:
                        raise ValueError('Invalid graph source output')
                    node[category][port]=dict(node=visit(source))
                    if str(connection[1])!='out': node[category][port]['output']=str(connection[1])
                else:
                    value=attr.Get(time)
                    if value is None and category=='inputs' and default is None: continue
                    if value_type=='asset':
                        from substrate_textures import resolve_asset
                        value=resolve_asset(attr.GetAttr(),value,time)
                    node[category][port]=list(value) if value_type in ('color3','vector2','color4') else value
        return name
    root=visit(UsdShade.Shader(terminal[0].GetPrim()))
    return validate(dict(version=1,output=root,nodes=nodes))
