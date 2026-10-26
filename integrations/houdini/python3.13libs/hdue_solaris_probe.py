"""Native Solaris compositor probe, with no secondary image window."""
import json
import time
import traceback
import math
import ctypes as C
import os
from pathlib import Path
import numpy as np
import hou
import hdefereval
from PySide6 import QtCore,QtGui
import hdue_houdini as integration

_test=None
class Probe:
    def __init__(self,host):
        self.host=host; self.deadline=time.monotonic()+180
        self.timer=QtCore.QTimer(); self.timer.timeout.connect(self.advance)
        self.steps=self.run(); self.timer.start(50)

    def advance(self):
        try:
            if integration._failure: raise RuntimeError(integration._failure)
            if time.monotonic()>self.deadline: raise TimeoutError(str(self.host.api.status()))
            next(self.steps)
        except StopIteration: self.finish()
        except BaseException:
            (self.host.log_dir/'test-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
            self.finish()

    def finish(self):
        self.timer.stop(); self.host.close()
        hdefereval.execute_deferred(lambda:hou.hscript('quit -f'))

    def run(self):
        viewer=integration.scene_viewer()
        for _ in range(20): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        for _ in range(8): yield
        results=[]
        previous=0
        for mode,label in ((0,'lumen'),(1,'pathtraced')):
            if mode: integration.set_mode('pathtraced')
            while True:
                status=self.host.api.status()
                if status['error']: raise RuntimeError(status['error'])
                if status['active'] and status['mode']==mode and status['generation']>previous and status['frame']['frame_id'] and status['frame']['converged']: break
                yield
            if mode and status['frame']['completed_samples']<64: raise AssertionError('Path Tracer did not reach 64 samples')
            depth=self.host.api.depth_pixels(status)
            if depth is None: raise AssertionError('No completed UE depth image')
            positive=[value for value in depth if value>0]
            if not positive or not all(math.isfinite(value) for value in depth) or max(positive)-min(positive)<1:
                raise AssertionError('Depth is missing, constant, or non-finite')
            from asset_runtime import Assets,AssetSceneStatus
            assets=AssetSceneStatus(); assets.struct_size=C.sizeof(assets)
            self.host.runtime.check(Assets(self.host.runtime).get_scene(status['session'],C.byref(assets)),'native scene status')
            result=dict(status=status,depth_cm_min=min(positive),depth_cm_max=max(positive),depth_hits=len(positive),depth_background=len(depth)-len(positive),
                        native_assets={n:getattr(assets,n) for n,_ in assets._fields_ if n!='message'})
            result.update((yield from self.capture(viewer,label)))
            results.append(result); previous=status['generation']
        from pxr import UsdGeom
        report=dict(passed=True,frames=results,up_axis=str(UsdGeom.GetStageUpAxis(viewer.stage())),meters_per_unit=UsdGeom.GetStageMetersPerUnit(viewer.stage()))
        (self.host.log_dir/'probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

    def capture(self,viewer,label):
        output=self.host.log_dir/(label+'-solaris.png')
        def capture():
            settings=viewer.flipbookSettings().stash()
            settings.frameRange((hou.frame(),hou.frame())); settings.outputToMPlay(False)
            settings.beautyPassOnly(False)
            settings.useResolution(False); settings.output(str(output))
            viewer.flipbook(viewer.curViewport(),settings,open_dialog=False)
        hdefereval.execute_deferred(capture)
        while not output.is_file(): yield
        image=QtGui.QImage(str(output)).convertToFormat(QtGui.QImage.Format_RGBA8888)
        data=bytes(image.constBits()); stride=image.bytesPerLine()
        red=sum(data[y*stride+x*4]>30 and data[y*stride+x*4]>data[y*stride+x*4+1]*1.5 and data[y*stride+x*4]>data[y*stride+x*4+2]*1.5
                for y in range(50,image.height()-30,2) for x in range(30,image.width()-30,2))
        pixels=np.frombuffer(data,dtype=np.uint8).reshape(image.height(),stride)[:,:image.width()*4].reshape(image.height(),image.width(),4)
        lit=int((pixels[40:-30,30:-30,:3].max(axis=2)>35).sum())
        result=dict(width=image.width(),height=image.height(),red_pixels=red,lit_pixels=lit,native_viewport=lit>image.width()*image.height()*.005)
        if Path(os.environ.get('HDUE_HOUDINI_INPUT','')).name=='smoke.usda' and red<image.width()*image.height()*.0001:
            raise AssertionError('No rendered red cube in native Solaris capture: '+str(red))
        if not result['native_viewport']: raise AssertionError('Native Solaris capture is empty')
        return result

def start(host):
    global _test
    _test=Probe(host)
