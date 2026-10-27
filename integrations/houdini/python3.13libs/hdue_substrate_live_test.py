"""Compile topology edits while the same Solaris process/session keeps ticking."""
import os
import time
import hou
import hdue_houdini as integration
import hdue_substrate
from hdue_materialx_test import Test as MaterialTest

_test=None


class Test(MaterialTest):
    def run(self):
        self.deadline=time.monotonic()+360
        viewer=integration.scene_viewer()
        for _ in range(40): yield
        while not self.host.prepared_materials.applied:
            if self.host.material_error: raise ValueError(self.host.material_error)
            yield
        controls=integration.current_settings_node(); integration.configure_live_preparation(controls)
        controls.parm('hdue_substrate_auto_prepare').set(1)
        network=hou.node(hou.node('/stage').userData('hdue:startupBuilder'))
        self.session=self.api.status()['session']; self.pid=os.getpid()
        original=yield from self.image('01-before-topology-edit',0)
        live=self.host.live_substrate
        tint=network.createNode(hdue_substrate.type_name('constant_color'),'live_tint')
        tint.parmTuple('value').set((.8,.025,.01)); network.node('base').setInput(0,tint)
        ticks=self.host.runtime.ticks
        yield from self.ready(1)
        added=yield from self.image('02-node-added-lumen',0,self.frames[-1]['generation'])
        if sum(abs(a-b) for a,b in zip(original,added))/len(added)<1e-4:
            raise AssertionError('The newly compiled topology did not change the image')
        if self.host.runtime.ticks-ticks<20: raise AssertionError('UE did not tick during background preparation')
        started=live.started
        tint.parmTuple('value').set((.02,.6,.04))
        edited=yield from self.image('03-added-node-live-value',0,self.frames[-1]['generation'])
        if sum(abs(a-b) for a,b in zip(added,edited))/len(edited)<1e-4:
            raise AssertionError('Generated parameters on the new material did not update live')
        if live.started!=started: raise AssertionError('A live parameter value unnecessarily recompiled the graph')
        integration.set_mode('pathtraced')
        yield from self.image('04-new-shader-pathtraced',1,self.frames[-1]['generation'])
        # Start B, then replace it with C while the editor is compiling. B may
        # populate the variant cache but must never become the current material.
        network.node('base').setInput(3,None)
        while not live.active:
            if live.phase=='failed': raise AssertionError(live.message)
            yield
        amount=network.createNode(hdue_substrate.type_name('constant'),'new_coverage')
        amount.parm('value').set(.35); network.node('masked_coat').setInput(1,amount)
        while not self.host.material_error: yield
        while live.completed<3 or self.host.material_error or self.api.status()['error']:
            if live.phase=='failed': raise AssertionError(live.message)
            if live.completed==2 and self.api.status()['frame']['frame_id']:
                raise AssertionError('A superseded shader was published as the latest edit')
            yield
        yield from self.image('05-latest-topology-pathtraced',1,self.frames[-1]['generation'])
        if live.started!=3: raise AssertionError('Topology jobs were not single-flight/coalesced: '+repr(live.status()))
        cached=live.started
        network.node('base').setInput(3,network.node('normal'))
        network.node('masked_coat').setInput(1,network.node('image_mask'))
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('06-cached-topology-restored',1,self.frames[-1]['generation'])
        if live.started!=cached: raise AssertionError('Restoring a cached topology started another cook')
        # An incomplete edit is not a compiler job; reconnecting resumes in place.
        network.node('layer').setInput(0,None)
        while not self.host.material_error: yield
        for _ in range(40): yield
        if live.started!=cached or not self.host.running: raise AssertionError('Incomplete graph was compiled or stopped the host')
        network.node('layer').setInput(0,network.node('masked_coat'))
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('07-incomplete-edit-recovered',1,self.frames[-1]['generation'])
        network.node('base').setInput(0,network.node('base_color'))
        integration.set_mode('realtime')
        while self.host.material_error or self.api.status()['error']: yield
        yield from self.image('08-original-topology-restored',0,self.frames[-1]['generation'])
        yield from self.capture('08-live-topology-solaris.png')
        self.stats=dict(live=live.status(),ue_initializations=self.host.runtime.initializations,
                        same_process=os.getpid()==self.pid,same_session=self.api.status()['session']==self.session)
        if self.host.runtime.initializations!=1 or not self.stats['same_session']:
            raise AssertionError('Live shader replacement restarted UE or its Hydra session')

    def ready(self,count):
        while self.host.live_substrate.completed<count or self.host.material_error or self.api.status()['error']:
            if self.host.live_substrate.phase=='failed': raise AssertionError(self.host.live_substrate.message)
            yield


def start(host):
    global _test
    _test=Test(host)
