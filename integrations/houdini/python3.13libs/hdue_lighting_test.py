"""HDR environment and MegaLights verification in the real Solaris viewport."""
import ctypes as C
import threading
import hou
from pxr import UsdGeom
from lighting_api import Lighting,Status
import hdue_houdini as integration
from hdue_houdini_test import Test as ViewportTest

_test=None

class Test(ViewportTest):
    def lighting(self,mode,domes=1):
        status=self.lights.status(self.api.status()['session'])
        if not status['mega_lights_project'] or not status['mega_lights_allowed']:
            raise AssertionError('MegaLights project settings are disabled')
        if status['mega_lights_active']!=(mode==0): raise AssertionError('Incorrect MegaLights rendering path')
        if status['environment_pending'] or status['dome_count']!=domes: raise AssertionError('Environment was not ready')
        if domes and status['environment_max_radiance']<=1: raise AssertionError('HDR lighting was clamped')
        self.frames[-1]['lighting']=status

    def run(self):
        self.lights=Lighting(self.host.runtime)
        viewer=integration.scene_viewer()
        import hdefereval
        for _ in range(24): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        for _ in range(8): yield
        original=yield from self.image('01-hdri-lumen',0,minimum_meshes=2)
        self.lighting(0)
        if UsdGeom.GetStageUpAxis(viewer.stage())!='Y' or UsdGeom.GetStageMetersPerUnit(viewer.stage())!=1:
            raise AssertionError('Solaris units changed')
        status=Status(); status.struct_size=C.sizeof(status)
        session=self.api.status()['session']
        if self.lights.get_status(session+1,C.byref(status))!=-1: raise AssertionError('Stale session accepted')
        status.struct_size-=1
        if self.lights.get_status(session,C.byref(status))!=-1: raise AssertionError('Short status accepted')
        status.struct_size=C.sizeof(status); result=[]
        worker=threading.Thread(target=lambda:result.append(self.lights.get_status(session,C.byref(status))))
        worker.start(); worker.join()
        if result!=[-3]: raise AssertionError('Lighting diagnostics left the owner thread')
        yield from self.capture('01-hdri-lumen-solaris.png')
        integration.set_mode('pathtraced')
        yield from self.image('02-hdri-pathtraced',1,self.frames[-1]['generation'])
        self.lighting(1)
        yield from self.capture('02-hdri-pathtraced-solaris.png')
        settings=integration.current_settings_node()
        edit=settings.parent().createNode('pythonscript','hdue_environment_test'); edit.setInput(0,settings)
        prefix='from pxr import UsdLux,UsdGeom,Gf\nstage=hou.pwd().editableStage()\n'
        dome=next(p for p in viewer.stage().Traverse() if p.GetTypeName()=='DomeLight')
        path=str(dome.GetPath())
        edit.parm('python').set(prefix+'p=stage.GetPrimAtPath('+repr(path)+')\n'
            +'UsdGeom.Xformable(p).AddRotateYOp(opSuffix="hdueTest").Set(180.)\n'
            +'UsdLux.DomeLight(p).GetExposureAttr().Set(-1.)')
        edit.setDisplayFlag(True); integration.set_mode('realtime')
        rotated=yield from self.image('03-hdri-rotated',0,self.frames[-1]['generation'])
        self.lighting(0)
        if sum(abs(a-b) for a,b in zip(original,rotated))/len(original)<.001:
            raise AssertionError('HDRI edit did not change the image')
        edit.parm('python').set(prefix+'UsdGeom.Imageable(stage.GetPrimAtPath('+repr(path)+')).MakeInvisible()')
        yield from self.image('04-hdri-hidden',0,self.frames[-1]['generation'])
        self.lighting(0,0)
        edit.parm('python').set(prefix+'pass')
        integration.set_mode('pathtraced')
        yield from self.image('05-hdri-restored-pathtraced',1,self.frames[-1]['generation'])
        self.lighting(1)

def start(host):
    global _test
    _test=Test(host)
