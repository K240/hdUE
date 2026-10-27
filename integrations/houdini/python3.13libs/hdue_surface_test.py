"""Wet overrides and live mask updates through the real Solaris event loop."""
import ctypes as C
import threading
import hou
from pxr import UsdGeom
import hdue_houdini as integration
from hdue_materialx_test import Test as MaterialTest
from hdue_viewport import Surfaces
from surface_api import SurfaceApi,Api,Batch

_test=None


class Test(MaterialTest):
    def run(self):
        import hdefereval
        import hdue_surface
        viewer=integration.scene_viewer()
        for _ in range(30): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        while not self.host.prepared_materials.applied:
            if self.host.material_error: raise ValueError(self.host.material_error)
            yield
        self.surface=SurfaceApi(self.host.runtime)
        self.stats={}
        for _ in range(40): yield
        original=yield from self.image('01-lumen',0)
        session=self.api.status()['session']
        self.stats['initial']=self.surface.status(session)
        if self.stats['initial']['binding_count']!=3: raise AssertionError('Expected wet sphere and two masked subset partitions')
        generation=self.frames[-1]['generation']
        integration.set_mode('pathtraced')
        yield from self.image('02-pathtraced',1,generation)
        yield from self.capture('02-pathtraced-solaris.png')
        controls=integration.current_settings_node()
        override=hdue_surface.create(['/World/Wet'],input_node=controls)
        override.parm('hdue_wet_amount').set(.35)
        yield from self.image('03-amount-edit',1,self.frames[-1]['generation'])
        if self.surface.status(session)['geometry_uploads']!=self.stats['initial']['geometry_uploads']:
            raise AssertionError('Scalar edit uploaded geometry')
        edit=override.parent().createNode('pythonscript','wet_mask_edit'); edit.setInput(0,override)
        edit.parm('python').set('from pxr import UsdGeom, Vt\ns=hou.pwd().editableStage()\n'
            'm=UsdGeom.Mesh(s.GetPrimAtPath("/World/Ramp/Sphere"))\n'
            'UsdGeom.PrimvarsAPI(m).GetPrimvar("wetness").Set(Vt.FloatArray([.5]*len(m.GetPointsAttr().Get())))')
        before=self.surface.status(session)
        edit.setDisplayFlag(True); edit.cook(force=True); viewer.curViewport().draw()
        yield from self.image('04-mask-edit',1,self.frames[-1]['generation'])
        after=self.surface.status(session); self.stats['mask_edit']=after
        if before['geometry_uploads']!=after['geometry_uploads'] or after['mask_uploads']-before['mask_uploads']!=2:
            raise AssertionError('Mask update did not use only the two subset color buffers: '+repr((before,after)))
        controls.parm('hdue_wet_mask_preview').set(1)
        yield from self.image('05-mask-preview',1,self.frames[-1]['generation'])
        yield from self.capture('05-mask-preview-solaris.png')
        controls.parm('hdue_wet_mask_preview').set(0)
        yield from self.image('06-shaded-restored',1,self.frames[-1]['generation'])
        invalid=Surfaces(C.sizeof(Surfaces),2,session+1,0,0,None,b'')
        if self.api.set_surfaces(C.byref(invalid))!=-4: raise AssertionError('Stale surface session accepted')
        invalid.session=session; invalid.abi_version=99
        if self.api.set_surfaces(C.byref(invalid))!=-1: raise AssertionError('Unknown viewport surface version accepted')
        invalid.abi_version=2; result=[]
        worker=threading.Thread(target=lambda:result.append(self.api.set_surfaces(C.byref(invalid))))
        worker.start(); worker.join()
        if result!=[-3]: raise AssertionError('Surface update left the UI thread')
        empty=Batch(C.sizeof(Batch),0,None)
        if self.surface.apply(session+1,C.byref(empty))!=-1: raise AssertionError('Backend accepted a stale session')
        table=Api()
        if self.surface.get(99,C.sizeof(table),C.byref(table))!=-2: raise AssertionError('Unknown surface ABI accepted')
        override.parm('hdue_wet_mask').set('missing_mask')
        while not self.host.material_error: yield
        for _ in range(10): yield
        if self.api.status()['frame']['frame_id']: raise AssertionError('Invalid wet settings reported a completed frame')
        override.parm('hdue_wet_enabled').set(0)
        override.cook(force=True); viewer.curViewport().draw()
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('07-disable-recovery',1,self.frames[-1]['generation'])
        integration.set_mode('realtime')
        yield from self.image('08-lumen-restored',0,self.frames[-1]['generation'])
        yield from self.capture('08-lumen-restored-solaris.png')
        self.stats['final']=self.surface.status(session)


def start(host):
    global _test
    _test=Test(host)
