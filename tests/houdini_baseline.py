"""UI-only baseline, deliberately does not import or initialize Unreal."""
from pathlib import Path
import os
import hou
from PySide6 import QtCore
import hdefereval
import json
from pxr import UsdGeom,Usd

def start():
    root=Path(os.environ['HDUE_TEST_ROOT']); output=Path(os.environ['HDUE_TEST_OUTPUT'])
    network=hou.node('/stage')
    source=network.createNode('sublayer'); source.parm('filepath1').set(str(root/'samples/smoke.usda'))
    config=network.createNode('configurelayer'); config.setInput(0,source)
    for name,value in {'setmetersperunit':1,'metersperunit':.01,'setupaxis':1,'upaxis':'z'}.items(): config.parm(name).set(value)
    config.setDisplayFlag(True); config.setCurrent(True,clear_all_selected=True)
    viewer=hou.ui.paneTabOfType(hou.paneTabType.SceneViewer); viewer.setPwd(network)
    viewer.setHydraRenderer('Storm'); viewer.curViewport().frameAll()
    def capture():
        config.cook(force=True)
        viewport=viewer.curViewport(); camera=viewport.defaultCamera()
        result=dict(node=str(viewer.currentNode()),stage=str(viewer.stage()),
                    translation=camera.translation(),rotation=str(camera.rotation()),
                    view=str(viewport.viewTransform()),clip=camera.clipPlanes(),
                    stage_locked=viewer.sceneGraphStageLocked(),prims=[str(p.GetPath()) for p in viewer.stage().Traverse()])
        try:
            viewport.setCamera(config,'/Camera'); result['set_camera']='success'
        except BaseException as error: result['set_camera']=str(error)
        # Match the authored camera directly for this independent baseline.
        stage=config.stage(); matrix=UsdGeom.Camera(stage.GetPrimAtPath('/Camera')).ComputeLocalToWorldTransform(Usd.TimeCode(1))
        camera.setRotation(hou.Matrix3(tuple(matrix[i][j] for i in range(3) for j in range(3))))
        camera.setTranslation(hou.Vector3(tuple(matrix.ExtractTranslation())))
        viewport.draw()
        (output/'diagnostics.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        settings=viewer.flipbookSettings().stash(); settings.frameRange((1,1)); settings.outputToMPlay(False)
        settings.useResolution(False); settings.output(str(output/'storm.png'))
        viewer.flipbook(viewer.curViewport(),settings,open_dialog=False)
        hou.hipFile.save(str(output/'baseline.hip'))
        hdefereval.execute_deferred(lambda:hou.hscript('quit -f'))
    QtCore.QTimer.singleShot(4000,lambda:hdefereval.execute_deferred(capture))
