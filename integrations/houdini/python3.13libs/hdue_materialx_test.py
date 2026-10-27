"""Native Solaris verification for a caller-supplied cooked MaterialX library."""
import ctypes as C
import json
from pathlib import Path
import threading
import hou
from pxr import UsdGeom,UsdShade
from PySide6 import QtGui
import hdue_houdini as integration
from hdue_houdini_test import Test as ViewportTest
from hdue_viewport import Materials
from material_api import MaterialApi

_test=None

class Test(ViewportTest):
    def run(self):
        import hdefereval
        viewer=integration.scene_viewer()
        for _ in range(30): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        while not self.host.prepared_materials.library or not self.host.prepared_materials.applied:
            if self.host.material_error: raise ValueError(self.host.material_error)
            yield
        assets=self.host.prepared_materials.settings['nativeMaterialAssets']
        if not assets: raise AssertionError('No prepared materials were bound')
        original=yield from self.image('01-lumen',0)
        self.stats={path:MaterialApi(self.host.runtime).material_stats(self.api.status()['session'],path) for path in assets}
        if not all(s['substrate_enabled'] for s in self.stats.values()): raise AssertionError('Native Substrate binding missing')
        if not any(s['texture_parameters'] for s in self.stats.values()): raise AssertionError('No native textures bound')
        state=self.api.status()
        invalid=Materials(C.sizeof(Materials),99,state['session'],0,0,None)
        if self.api.set_materials(C.byref(invalid))!=-1: raise AssertionError('Unknown material ABI accepted')
        invalid.struct_size-=1
        if self.api.set_materials(C.byref(invalid))!=-1: raise AssertionError('Short material descriptor accepted')
        invalid.struct_size=C.sizeof(Materials)
        invalid.abi_version=1; invalid.session=state['session']+1
        if self.api.set_materials(C.byref(invalid))!=-4: raise AssertionError('Stale material session accepted')
        invalid.session=state['session']; result=[]
        worker=threading.Thread(target=lambda:result.append(self.api.set_materials(C.byref(invalid))))
        worker.start(); worker.join()
        if result!=[-3]: raise AssertionError('Material update left the owner thread')
        yield from self.capture('01-lumen-solaris.png')
        generation=self.frames[-1]['generation']
        integration.set_mode('pathtraced')
        yield from self.image('02-pathtraced',1,generation)
        yield from self.capture('02-pathtraced-solaris.png')
        # Exercise actual Hydra resync and stale partition cleanup. All edits
        # live in a generated LOP; the caller's source remains unchanged.
        stage=viewer.stage()
        subsets=[subset for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)
                 for subset in UsdShade.MaterialBindingAPI(prim).GetMaterialBindSubsets()]
        if subsets and len(assets)>1:
            subset=max(subsets,key=lambda s:len(s.GetIndicesAttr().Get()))
            bound=UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial()[0]
            alternate=max((path for path in assets if path!=str(bound.GetPath())),key=lambda path:self.stats[path]['vector_parameters'])
            settings=integration.current_settings_node()
            edit=settings.parent().createNode('pythonscript','hdue_subset_verification')
            edit.setInput(0,settings)
            edit.parm('python').set('import hou\nfrom pxr import Sdf\nstage=hou.pwd().editableStage()\n'
                +'stage.GetPrimAtPath('+repr(str(subset.GetPath()))+').GetRelationship("material:binding").SetTargets([Sdf.Path('+repr(alternate)+')])')
            edit.setDisplayFlag(True)
            integration.set_mode('realtime')
            edited=yield from self.image('03-subset-rebound',0,self.frames[-1]['generation'])
            if sum(abs(a-b) for a,b in zip(original,edited))/len(original)<.0001:
                raise AssertionError('GeomSubset material edit did not reach the image')
            edit.parm('python').set('import hou\nstage=hou.pwd().editableStage()\nstage.OverridePrim('+repr(str(subset.GetPath()))+').SetActive(False)')
            edit.cook(force=True); viewer.curViewport().draw()
            yield from self.image('04-subset-removed',0,self.frames[-1]['generation'])
            settings.setDisplayFlag(True); edit.destroy(); viewer.curViewport().draw()
            yield from self.image('05-subset-restored',0,self.frames[-1]['generation'])
            if self.frames[-1]['meshes']!=self.frames[0]['meshes']:
                raise AssertionError('Stale subset partitions survived restoration')

    def capture(self,name):
        import hdefereval
        viewer=integration.scene_viewer(); path=self.output/name
        def flipbook():
            settings=viewer.flipbookSettings().stash()
            settings.frameRange((hou.frame(),hou.frame())); settings.outputToMPlay(False)
            settings.beautyPassOnly(False); settings.useResolution(False); settings.output(str(path))
            viewer.flipbook(viewer.curViewport(),settings,open_dialog=False)
        hdefereval.execute_deferred(flipbook)
        while not path.is_file(): yield
        image=QtGui.QImage(str(path))
        if image.isNull(): raise AssertionError('Solaris compositor capture failed')
        self.captures.append(dict(file=name,width=image.width(),height=image.height()))

    def finish(self):
        if self.verified: self.verified['material_stats']=self.stats
        super().finish()

def start(host):
    global _test
    _test=Test(host)
