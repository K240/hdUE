import json
import hou
for name in ('_captureFramebuffer','setCamera','frameAll'):
    method=getattr(hou.GeometryViewport,name,None)
    if method: print(name,method.__doc__)
print('viewport_settings',json.dumps([n for n in dir(hou.GeometryViewportSettings) if any(t in n.lower() for t in ('hydra','render','parm','color'))]))
print('viewer_settings',json.dumps([n for n in dir(hou.SceneViewer) if any(t in n.lower() for t in ('stat','setting','parm'))]))
node=hou.node('/stage').createNode('pythonscript','probe')
print('python_lop_parms',[p.name() for p in node.parms()])
