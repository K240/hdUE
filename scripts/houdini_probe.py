"""Read SDK/HOM capabilities under uv run -- <HFS>/bin/hython.exe."""
import json
import hou
from pxr import Usd, UsdImagingGL
print(json.dumps(dict(houdini=hou.applicationVersionString(),usd=Usd.GetVersion(),
    scene_viewer_methods=[n for n in dir(hou.SceneViewer) if any(t in n.lower() for t in ('hydra','lop','render'))],
    viewport_methods=[n for n in dir(hou.GeometryViewport) if any(t in n.lower() for t in ('render','save','capture','camera'))]),indent=2))
for name in ('setHydraRenderer','currentHydraRenderer','hydraRenderers','lopNode','setCurrentState'):
    value=getattr(hou.SceneViewer,name,None)
    if value: print(name,value.__doc__)
print('ui renderer methods',[n for n in dir(hou.ui) if 'render' in n.lower()])
