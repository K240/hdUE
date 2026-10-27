"""Exercise a caller-supplied asset referenced downstream of hdUE settings."""
import os
import numpy as np
import hou
from pxr import UsdGeom
import hdue_houdini as integration
from hdue_houdini_test import Test as ViewportTest
from material_api import MaterialApi

_test=None

class Test(ViewportTest):
    def run(self):
        import hdefereval
        source=os.environ['HDUE_HOUDINI_INPUT']
        network=hou.node('/stage')
        base=network.createNode('sublayer','reference_environment')
        base.parm('filepath1').set(str(integration.ROOT/'samples/lighting/lighting.usda'))
        settings=integration.settings_node(base)
        reference=network.createNode('reference','reference_asset')
        reference.setInput(0,settings)
        reference.parm('filepath1').set(source)
        reference.parm('primpath1').set('/ReferencedAsset')
        reference.parm('instanceable1').set(1)
        edit=network.createNode('pythonscript','reference_transform')
        edit.setInput(0,reference)
        def move(x):
            edit.parm('python').set('from pxr import UsdGeom\nstage=hou.pwd().editableStage()\n'
                +'UsdGeom.Xformable(stage.GetPrimAtPath("/ReferencedAsset")).AddTranslateOp(opSuffix="test").Set(('+str(x)+',0,0))')
            edit.setDisplayFlag(True); edit.setCurrent(True,clear_all_selected=True)
        move(2)
        viewer=integration.scene_viewer(); viewer.setPwd(network)
        for _ in range(30): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        for _ in range(8): yield
        first=yield from self.image('01-reference-lumen',0,minimum_meshes=6)
        if self.host.material_error: raise AssertionError(self.host.material_error)
        assets=self.host.prepared_materials.settings.get('nativeMaterialAssets',{})
        expected={path:asset for path,asset in assets.items() if path.startswith('/ReferencedAsset/')}
        if not expected: raise AssertionError('Referenced MaterialX materials were not mapped: '+repr(assets))
        self.stats={path:MaterialApi(self.host.runtime).material_stats(self.api.status()['session'],path) for path in expected}
        if not any(s['texture_parameters'] for s in self.stats.values()): raise AssertionError('Referenced textures were not bound')
        yield from self.capture('01-reference-lumen-solaris.png')
        generation=self.frames[-1]['generation']; submissions=self.frames[-1]['scene_submissions']
        move(-2)
        moved=yield from self.image('02-reference-translated',0,generation)
        self.depth_changed('01-reference-lumen','02-reference-translated')
        if self.frames[-1]['scene_submissions']<=submissions: raise AssertionError('Reference transform did not submit geometry')
        if sum(abs(a-b) for a,b in zip(first,moved))/len(first)<.001:
            raise AssertionError('Reference transform did not change the image')
        generation=self.frames[-1]['generation']
        integration.set_mode('pathtraced')
        traced=yield from self.image('03-reference-pathtraced',1,generation)
        yield from self.capture('03-reference-pathtraced-solaris.png')
        move(2)
        traced_moved=yield from self.image('04-reference-pathtraced-edited',1,self.frames[-1]['generation'])
        self.depth_changed('03-reference-pathtraced','04-reference-pathtraced-edited')
        if sum(abs(a-b) for a,b in zip(traced,traced_moved))/len(traced)<.001:
            raise AssertionError('Reference transform did not change the Path Tracer image')

    def depth_changed(self,before,after):
        def read(label):
            with (self.output/(label+'-depth.pfm')).open('rb') as stream:
                stream.readline(); stream.readline(); stream.readline()
                return np.frombuffer(stream.read(),dtype='<f4')
        changed=int((np.abs(read(before)-read(after))>1).sum())
        if changed<100: raise AssertionError('Referenced geometry did not move in the depth image')
        self.frames[-1]['changed_depth_pixels']=changed

    def capture(self,name):
        from hdue_materialx_test import Test
        return (yield from Test.capture(self,name))

    def finish(self):
        if self.verified: self.verified['material_stats']=self.stats
        super().finish()

def start(host):
    global _test
    _test=Test(host)
