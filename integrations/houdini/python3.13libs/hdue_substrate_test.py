"""Verify VOP authoring, native shaders and live Solaris Substrate updates."""
import ctypes as C
import hou
from pxr import UsdGeom
import hdue_houdini as integration
from hdue_materialx_test import Test as MaterialTest
from material_api import MaterialApi
from surface_api import SurfaceApi

_test=None


class Test(MaterialTest):
    def run(self):
        import hdefereval
        import hdue_substrate
        viewer=integration.scene_viewer()
        for _ in range(30): yield
        controls=integration.current_settings_node(); integration.configure_live_preparation(controls)
        controls.parm('hdue_substrate_auto_prepare').set(0)
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        while not self.host.prepared_materials.applied:
            if self.host.material_error: raise ValueError(self.host.material_error)
            yield
        for _ in range(40): yield
        initial=yield from self.image('01-vop-lumen',0)
        session=self.api.status()['session']; surface=SurfaceApi(self.host.runtime)
        self.stats={'initial':surface.status(session)}
        material=MaterialApi(self.host.runtime).material_stats(session,'/Looks/WaterCoating')
        if material['closure_count']!=3 or material['bytes_per_pixel']>128: raise AssertionError('The authored water layer was simplified: '+repr(material))
        self.stats['compiled']=material
        integration.set_mode('pathtraced')
        original=yield from self.image('02-vop-pathtraced',1,self.frames[-1]['generation'])
        controls=integration.current_settings_node()
        network=hdue_substrate.create(preset='wet',material='/Looks/SS_Coat')
        author=hdue_substrate.author_lop(network,'/Looks/WaterCoating',controls)
        author.cook(force=True)
        for _ in range(15): yield
        # Edit an actual VOP parameter. Its registered dependency recooks USD.
        before=surface.status(session)
        generation=self.api.status()['generation']
        network.node('amount').parm('value').set(.35)
        changed=yield from self.image('03-vop-coverage',1,generation)
        if sum(abs(a-b) for a,b in zip(original,changed))/len(original)<1e-6:
            raise AssertionError('Published VOP parameter did not change the image')
        after=surface.status(session)
        if before['geometry_uploads']!=after['geometry_uploads'] or before['mask_uploads']!=after['mask_uploads']:
            raise AssertionError('A public scalar edit uploaded mesh or mask data')
        network.node('roughness').parm('value').set(.3)
        yield from self.image('04-vop-roughness',1,self.frames[-1]['generation'])
        network.node('layer').parm('parameter_blending').set(1)
        while not self.host.material_error: yield
        for _ in range(10): yield
        if self.api.status()['frame']['frame_id']: raise AssertionError('Unprepared graph was reported converged')
        network.node('layer').parm('parameter_blending').set(0)
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('05-graph-restored',1,self.frames[-1]['generation'])
        edit=author.parent().createNode('pythonscript','substrate_mask_edit'); edit.setInput(0,author)
        edit.parm('python').set('from pxr import UsdGeom,Vt\ns=hou.pwd().editableStage()\n'
            'm=UsdGeom.Mesh(s.GetPrimAtPath("/World/Ramp/Sphere"))\n'
            'UsdGeom.PrimvarsAPI(m).GetPrimvar("wetness").Set(Vt.FloatArray([.5]*len(m.GetPointsAttr().Get())))')
        before=surface.status(session); edit.setDisplayFlag(True); edit.cook(force=True); viewer.curViewport().draw()
        yield from self.image('06-vop-mask',1,self.frames[-1]['generation'])
        after=surface.status(session); self.stats['mask_edit']=after
        if after['geometry_uploads']!=before['geometry_uploads'] or after['mask_uploads']-before['mask_uploads']!=2:
            raise AssertionError('Mask edit did not update only the two subset color buffers')
        controls.parm('hdue_wet_mask_preview').set(1)
        yield from self.image('07-vop-mask-preview',1,self.frames[-1]['generation'])
        controls.parm('hdue_wet_mask_preview').set(0)
        integration.set_mode('realtime')
        yield from self.image('08-vop-lumen-restored',0,self.frames[-1]['generation'])
        yield from self.capture('08-vop-lumen-solaris.png')
        self.stats['final']=surface.status(session)
        # Leave an editable, verified VOP network in the saved test HIP.
        network.setCurrent(True,clear_all_selected=True)


def start(host):
    global _test
    _test=Test(host)
