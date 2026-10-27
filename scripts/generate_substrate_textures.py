"""Generate owned RGBA/normal fixtures and a directly textured Builder scene."""
import argparse
import math
import os
from pathlib import Path
import struct
import sys
import zlib
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from substrate_graph import author,outputs,NODES


def png(file,pixel,size=128):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    data=b''.join(b'\0'+bytes(c for x in range(size) for c in pixel(x,y,size)) for y in range(size))
    file.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+
                     chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b''))


def example_graph(folder):
    def texture(file,space):
        return dict(type='texture',inputs={'uv':{'node':'uv'}},properties={'file':str(folder/file),'color_space':space})
    return dict(version=1,output='layer',nodes={
        'uv':dict(type='uv',inputs={'scale':[2.,1.],'offset':[.125,0.]}),
        'base_color':texture('color.png','srgb'),
        'packed_data':texture('packed.png','raw'),
        'normal_texture':texture('normal.png','raw'),
        'roughness':dict(type='channel',inputs={'value':{'node':'packed_data','output':'rgba'}},properties={'channel':'g'}),
        'image_mask':dict(type='channel',inputs={'value':{'node':'packed_data','output':'rgba'}},properties={'channel':'a'}),
        'normal_strength':dict(type='scalar',properties={'parameter_name':'NormalStrength','value':1.}),
        'normal':dict(type='normalmap',inputs={'color':{'node':'normal_texture'},'strength':{'node':'normal_strength'}}),
        'base':dict(type='slab',inputs={'diffuse_albedo':{'node':'base_color'},'roughness':{'node':'roughness'},'normal':{'node':'normal'}}),
        'coat':dict(type='slab',inputs={'diffuse_albedo':[0.,0.,0.],'f0':[.02,.02,.02],'roughness':.06,'mfp':[1e6]*3},properties={'subsurface':'simple_volume'}),
        'masked_coat':dict(type='weight',inputs={'surface':{'node':'coat'},'weight':{'node':'image_mask'}}),
        'layer':dict(type='vertical',inputs={'top':{'node':'masked_coat'},'base':{'node':'base'}})})


def vops(parent,graph):
    import hdue_substrate
    network=hdue_substrate.create(parent)
    network.node('slab').destroy()
    nodes={name:network.createNode(hdue_substrate.type_name(n['type']),name) for name,n in graph['nodes'].items()}
    for name,node in graph['nodes'].items():
        vop=nodes[name]; schema=NODES[node['type']]
        for category in ('inputs','properties'):
            for port,value in node.get(category,{}).items():
                if isinstance(value,dict):
                    source=value['node']; index=list(schema['inputs']).index(port)
                    source_outputs=list(outputs(NODES[graph['nodes'][source]['type']]))
                    vop.setInput(index,nodes[source],source_outputs.index(value.get('output','out')))
                elif isinstance(value,(list,tuple)): vop.parmTuple(port).set(value)
                else: vop.parm(port).set(value)
    output=next(n for n in network.children() if n.type().name()=='subnetconnector')
    output.setInput(0,nodes[graph['output']]); network.layoutChildren()
    return network


def generate(folder,hip=None,runtime_config=None):
    folder=folder.resolve(); folder.mkdir(parents=True,exist_ok=True)
    file=folder/'textures.usda'
    if file.exists(): raise FileExistsError(file)
    images=folder/'textures'; images.mkdir(exist_ok=True)
    png(images/'color.png',lambda x,y,s: (210,85,25,255) if (x//16+y//16)%2 else (20,150,190,255))
    png(images/'packed.png',lambda x,y,s: (int(255*x/(s-1)),45 if (x//32+y//32)%2 else 200,128,255 if x<s//2 else 0))
    def normal(x,y,size):
        nx=.5*math.sin(math.tau*x/16); ny=.5*math.cos(math.tau*y/16); nz=math.sqrt(1-nx*nx-ny*ny)
        return tuple(round(127.5*(n+1)) for n in (nx,ny,nz))+(255,)
    png(images/'normal.png',normal)
    for tile,color in ((1001,(210,85,25,255)),(1002,(20,150,190,255))):
        png(images/f'udim.{tile}.png',lambda x,y,s,c=color:c)
    graph=example_graph(Path('textures'))
    stage=Usd.Stage.CreateNew(str(file)); stage.SetDefaultPrim(stage.DefinePrim('/World','Xform'))
    UsdGeom.SetStageUpAxis(stage,'Z'); UsdGeom.SetStageMetersPerUnit(stage,.01)
    placement=UsdGeom.Xform.Define(stage,'/World/Textured')
    placement.GetPrim().GetReferences().AddReference(os.path.relpath(ROOT/'samples/wet_surface/asset.usda',folder).replace('\\','/'))
    placement.AddTranslateOp().Set(Gf.Vec3d(0,0,60))
    material=author(stage,'/Looks/Textured',graph)
    UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath('/World/Textured/Sphere')).Bind(material)
    target=Gf.Vec3d(0,0,60); eye=Gf.Vec3d(70,-260,140)
    camera=UsdGeom.Camera.Define(stage,'/Camera')
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye,target,Gf.Vec3d(0,0,1)).GetInverse())
    camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(20.25); camera.CreateFocalLengthAttr(40)
    camera.CreateClippingRangeAttr(Gf.Vec2f(1,5000))
    for name,pos,intensity,size in [('Key',Gf.Vec3d(-100,-250,350),12,(120,40)),('Fill',Gf.Vec3d(250,-100,180),1,(140,200))]:
        light=UsdLux.RectLight.Define(stage,'/Lights/'+name)
        light.CreateIntensityAttr(intensity); light.CreateWidthAttr(size[0]); light.CreateHeightAttr(size[1])
        light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(pos,target,Gf.Vec3d(0,0,1)).GetInverse())
    stage.GetRootLayer().Save()
    if hip: save_hip(file,hip,runtime_config)
    return stage


def save_hip(file,hip,runtime_config):
    if not runtime_config: raise ValueError('HIP output requires --runtime-config')
    os.environ['HDUE_RUNTIME_CONFIG']=str(runtime_config.resolve())
    sys.path.insert(0,str(ROOT/'integrations/houdini/python3.13libs'))
    import hou,hdue_houdini,hdue_substrate
    from substrate_graph import read,signature
    file=file.resolve(); stage=Usd.Stage.Open(str(file))
    graph=read(UsdShade.Material(stage.GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
    hip=hip.resolve(); hip.parent.mkdir(parents=True,exist_ok=True)
    if hip.exists(): raise FileExistsError(hip)
    hou.hipFile.setName(str(hip)); lopnet=hou.node('/stage')
    source=lopnet.createNode('sublayer','Texture_scene'); source.parm('filepath1').set(str(file))
    controls=hdue_houdini.settings_node(source,dict(metersperunit=.01,upaxis='Z'))
    library=lopnet.createNode('materiallibrary','MaterialLibrary'); library.setInput(0,controls)
    network=vops(library,graph)
    author_node=hdue_substrate.author_lop(network,'/Looks/Textured',library,('/World/Textured/Sphere',))
    author_node.setName('Author_Substrate_USD',unique_name=True)
    note=lopnet.createStickyNote('Texture_guide')
    note.setText('DIRECT SUBSTRATE TEXTURES\n\nOne mesh. RGB: sRGB base color.\nRaw G: roughness. Raw A: coat coverage.\nRaw normal RGB: Normal Map conversion.\n\nUV, Slab values, channels and normal controls update live.\nNode/wire edits prepare automatically in the background.\nThe new graph appears when compilation is ready.\nIncomplete wiring pauses safely; no Houdini restart is needed.')
    lopnet.layoutChildren(); lopnet.setUserData('hdue:startupBuilder',network.path())
    assert not author_node.errors() and author_node.stage()
    assert signature(hdue_substrate.graph(network))==signature(graph)
    hou.hipFile.save(str(hip))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=ROOT/'samples/substrate')
    parser.add_argument('--hip',type=Path); parser.add_argument('--runtime-config',type=Path); parser.add_argument('--scene',type=Path)
    args=parser.parse_args()
    if args.scene:
        if not args.hip: parser.error('--scene requires --hip')
        save_hip(args.scene,args.hip,args.runtime_config)
    else: generate(args.output,args.hip,args.runtime_config)
