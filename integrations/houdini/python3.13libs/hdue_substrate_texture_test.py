"""Exercise directly connected textures in an owned editable Solaris HIP."""
import hou
from pxr import UsdGeom
import hdue_houdini as integration
from hdue_materialx_test import Test as MaterialTest
from material_api import MaterialApi
from surface_api import SurfaceApi
_test=None


class Test(MaterialTest):
    def run(self):
        viewer=integration.scene_viewer()
        for _ in range(40): yield
        while not self.host.prepared_materials.applied:
            if self.host.material_error: raise ValueError(self.host.material_error)
            yield
        controls=integration.current_settings_node(); integration.configure_live_preparation(controls)
        controls.parm('hdue_substrate_auto_prepare').set(0)
        network=hou.node(hou.node('/stage').userData('hdue:startupBuilder'))
        if not network: raise AssertionError('The texture Builder was not loaded from HIP')
        initial=yield from self.image('01-texture-lumen',0,minimum_meshes=1)
        yield from self.capture('01-texture-lumen-solaris.png')
        session=self.api.status()['session']; surface=SurfaceApi(self.host.runtime)
        self.stats={'compiled':MaterialApi(self.host.runtime).material_stats(session,'/Looks/Textured')}
        initial_geometry=surface.status(session)['geometry_uploads']
        network.node('uv').parmTuple('scale').set((3,1))
        network.node('uv').parmTuple('offset').set((.3,0))
        uv=yield from self.image('01b-live-uv-lumen',0,self.frames[-1]['generation'])
        difference=sum(abs(a-b) for a,b in zip(initial,uv))/len(uv)
        if difference<1e-4: raise AssertionError('Direct UV scale/offset did not update the image')
        self.stats['live_uv_difference']=difference
        network.node('roughness').parm('channel').set('r')
        network.node('coat').parm('roughness').set(.35)
        network.node('coat').parmTuple('f0').set((.15,.08,.03))
        slab=yield from self.image('01c-live-slab-channel-lumen',0,self.frames[-1]['generation'])
        if sum(abs(a-b) for a,b in zip(uv,slab))/len(slab)<1e-4: raise AssertionError('Direct Slab/channel controls did not update')
        network.node('normal').parm('flip_green').set(0)
        flipped=yield from self.image('01d-live-flip-green-lumen',0,self.frames[-1]['generation'])
        if sum(abs(a-b) for a,b in zip(slab,flipped))/len(slab)<1e-6: raise AssertionError('Flip Green did not update')
        network.node('uv').parmTuple('scale').set((2,1)); network.node('uv').parmTuple('offset').set((.125,0))
        network.node('roughness').parm('channel').set('g'); network.node('coat').parm('roughness').set(.06)
        network.node('coat').parmTuple('f0').set((.02,.02,.02)); network.node('normal').parm('flip_green').set(1)
        yield from self.image('01e-live-controls-restored',0,self.frames[-1]['generation'])
        if surface.status(session)['geometry_uploads']!=initial_geometry: raise AssertionError('Automatic parameter edits uploaded geometry')
        integration.set_mode('pathtraced')
        original=yield from self.image('02-texture-pathtraced',1,self.frames[-1]['generation'])
        network.node('uv').parmTuple('offset').set((.4,0)); network.node('coat').parm('roughness').set(.35)
        direct=yield from self.image('02b-direct-controls-pathtraced',1,self.frames[-1]['generation'])
        if sum(abs(a-b) for a,b in zip(original,direct))/len(direct)<1e-4: raise AssertionError('Automatic parameters did not update Path Tracer')
        network.node('uv').parmTuple('offset').set((.125,0)); network.node('coat').parm('roughness').set(.06)
        original=yield from self.image('02c-direct-controls-restored',1,self.frames[-1]['generation'])
        before=surface.status(session); generation=self.api.status()['generation']
        author=hou.node('/stage/Author_Substrate_USD'); cooks=author.cookCount()
        for i in range(100): network.node('normal_strength').parm('value').set(1-(i+1)/100)
        if author.cookCount()!=cooks: raise AssertionError('A VOP callback forced cooks during a parameter-edit burst')
        flat=yield from self.image('03-normal-strength-zero',1,generation)
        difference=sum(abs(a-b) for a,b in zip(original,flat))/len(original)
        if difference<1e-5: raise AssertionError('Normal Map strength had no visible effect')
        after=surface.status(session)
        if before['geometry_uploads']!=after['geometry_uploads'] or before['mask_uploads']!=after['mask_uploads']:
            raise AssertionError('Normal-strength parameter edit uploaded mesh/mask data')
        self.stats.update(normal_difference=difference,before=before,after=after,burst_edits=100,burst_author_cooks=author.cookCount()-cooks)
        if author.cookCount()-cooks>2: raise AssertionError('Parameter edits did not settle into a bounded authoring update')
        network.node('normal_strength').parm('value').set(1)
        yield from self.image('04-normal-restored',1,self.frames[-1]['generation'])
        image=network.node('normal_texture'); source=image.evalParm('file')
        image.parm('file').set(network.node('base_color').evalParm('file'))
        while not self.host.material_error: yield
        for _ in range(8): yield
        if self.api.status()['frame']['frame_id']: raise AssertionError('Unprepared texture edit was reported converged')
        blocked=self.api.status(); cooks=author.cookCount()
        for _ in range(100): yield
        if author.cookCount()!=cooks or self.api.status()['generation']!=blocked['generation']:
            raise AssertionError('Unchanged preparation error retriggered export/render updates')
        self.stats['preparation_error_settled']=True
        image.parm('file').set(source)
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('05-texture-restored',1,self.frames[-1]['generation'])
        for consumer,index,upstream,label in ((network.node('layer'),0,network.node('masked_coat'),'surface'),
                (network.node('base'),3,network.node('normal'),'numeric')):
            consumer.setInput(index,None)
            import time
            deadline=time.monotonic()+6
            while not self.host.material_error:
                if time.monotonic()>deadline:
                    import hdue_substrate
                    from substrate_graph import signature,read
                    from pxr import UsdShade,Usd
                    exported=read(UsdShade.Material(author.stage().GetPrimAtPath('/Looks/Textured')),Usd.TimeCode.Default())
                    raise AssertionError('Disconnect not published: '+repr(dict(kind=label,cooks=author.cookCount(),
                        revision=author.evalParm(hdue_substrate.REVISION_PARM),direct=signature(hdue_substrate.graph(network)),
                        exported=signature(exported),callbacks=repr(consumer.eventCallbacks()),host_error=self.host.material_error)))
                yield
            for _ in range(12): yield
            if integration._failure or not self.host.running: raise AssertionError('Disconnecting a wire stopped the host')
            if self.api.status()['frame']['frame_id']: raise AssertionError('Disconnected graph published a completed image')
            consumer.setInput(index,upstream)
            while self.host.material_error or self.api.status()['error']: yield
            yield from self.image('05b-'+label+'-wire-restored',1,self.frames[-1]['generation'])
        network.node('uv').parmTuple('offset').set((.25,0))
        yield from self.image('05c-live-edit-after-reconnect',1,self.frames[-1]['generation'])
        network.node('uv').parmTuple('offset').set((.125,0))
        self.stats['disconnect_recovery_verified']=True
        integration.set_mode('realtime')
        yield from self.image('06-texture-lumen-restored',0,self.frames[-1]['generation'])
        yield from self.capture('06-texture-lumen-restored-solaris.png')

    def finish(self):
        if self.verified:
            stage=integration.scene_viewer().stage()
            self.verified.update(up_axis=UsdGeom.GetStageUpAxis(stage),meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage))
        super().finish()


def start(host):
    global _test
    _test=Test(host)
