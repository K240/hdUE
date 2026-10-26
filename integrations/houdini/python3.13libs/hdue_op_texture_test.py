"""Real Solaris viewport test for live op: texture updates in both render modes."""
import json
import sys
import time

import hou
import hdefereval
import numpy as np
from pxr import UsdGeom

import hdue_houdini as integration
import hdue_op_textures as textures
from hdue_solaris_probe import Probe

_test=None


class Test(Probe):
    def finish(self):
        self.timer.stop()
        self.host.close()
        if hasattr(self,'verified'):
            state=json.loads((self.host.log_dir/'runtime-state.json').read_text())
            if not state['closed'] or state['runtime_state']!=4 or state['ue_initializations']!=1:
                raise AssertionError('UE lifetime contract failed')
            self.verified['runtime']=state
            (self.host.log_dir/'verified.json').write_text(json.dumps(self.verified,indent=2),encoding='utf-8')
        hdefereval.execute_deferred(lambda:hou.hscript('quit -f'))

    def completed(self,label,mode,previous=0,channel=0):
        while True:
            state=self.host.api.status()
            if state['error']: raise RuntimeError(state['error'])
            if state['active'] and state['mode']==mode and state['generation']>previous and state['frame']['frame_id'] and state['frame']['converged']:
                data=self.host.api.pixels(state)
                depth=self.host.api.depth_pixels(state)
                if data and depth is not None:
                    frame,pixels=data
                    rgb=np.ctypeslib.as_array(pixels).reshape(-1,4)[:,:3]
                    hits=np.ctypeslib.as_array(depth)>0
                    if hits.any():
                        color=rgb[hits].mean(axis=0)
                        other=2 if channel==0 else 0
                        if color[channel]>color[other]*2 and color[channel]>.02:
                            break
            yield
        if mode and frame.completed_samples!=64:
            raise AssertionError('Path Tracer did not converge at 64 samples')
        self.frames.append(dict(label=label,status=state,mean_rgb=color.tolist(),texture_files=[e['path'] for e in textures.cache().entries.values()]))
        integration._report('Verified live COP texture '+label+': '+str(color))
        return state

    def run(self):
        sys.path.insert(0,str(integration.ROOT/'tests'))
        from houdini_op_textures import fixture,materialx_source
        obj,color,output,source=fixture('live_cop_texture')
        settings=integration.settings_node(source)
        settings.setDisplayFlag(True); settings.setCurrent(True,clear_all_selected=True)
        viewer=integration.scene_viewer(); viewer.setPwd(hou.node('/stage'))
        integration.activate()
        self.frames=[]; captures=[]
        for _ in range(20): yield
        hdefereval.execute_deferred(viewer.curViewport().frameAll)
        for _ in range(8): yield
        state=yield from self.completed('01-red-lumen',0)
        captures.append((yield from self.capture(viewer,'01-red-lumen')))
        integration.set_mode('pathtraced')
        state=yield from self.completed('02-red-pathtraced',1,state['generation'])
        captures.append((yield from self.capture(viewer,'02-red-pathtraced')))
        old_path=textures.cache().entries[('op:'+output.path(),False)]['path']
        # No forced LOP cook: the host must detect this upstream COP edit itself.
        color.parmTuple('f3').set((.02,.05,.8))
        state=yield from self.completed('03-blue-pathtraced',1,state['generation'],2)
        if old_path==textures.cache().entries[('op:'+output.path(),False)]['path']:
            raise AssertionError('COP edit did not replace the immutable texture path')
        captures.append((yield from self.capture(viewer,'03-blue-pathtraced')))
        integration.set_mode('realtime')
        state=yield from self.completed('04-blue-lumen',0,state['generation'],2)
        captures.append((yield from self.capture(viewer,'04-blue-lumen')))
        color.parmTuple('f3').set((.2,.03,.01))
        color.parm('f3r').setExpression('$F/10',hou.exprLanguage.Hscript)
        hou.setFrame(2); integration.set_mode('pathtraced')
        state=yield from self.completed('05-frame-2',1,state['generation'])
        before=self.frames[-1]['mean_rgb'][0]
        hou.setFrame(8)
        state=yield from self.completed('06-frame-8',1,state['generation'])
        if self.frames[-1]['mean_rgb'][0]<before*2:
            raise AssertionError('Frame-dependent COP did not update its pixels')
        edit=materialx_source(source)
        settings.setInput(0,edit)
        state=yield from self.completed('07-materialx-cop',1,state['generation'])
        captures.append((yield from self.capture(viewer,'07-materialx-cop')))
        self.verified=dict(passed=True,presentation='native Solaris viewport',frames=self.frames,captures=captures,
                           up_axis=str(UsdGeom.GetStageUpAxis(viewer.stage())),meters_per_unit=UsdGeom.GetStageMetersPerUnit(viewer.stage()))


def start(host):
    global _test
    _test=Test(host)
