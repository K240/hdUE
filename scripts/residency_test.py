"""Real GPU gates: no PT samples before all UDIM pages and mappings are ready."""
import hashlib
import time
from pathlib import Path
from pxr import Usd,UsdShade
from residency_api import ResidencyApi
from usdview_test import SmokeTest,PLUGIN

class ResidencyTest(SmokeTest):
    def __init__(self,*args):
        super().__init__(*args)
        self.deadline=time.monotonic()+600
        self.progress=[]
        self.last_progress=None

    def stats(self):
        stats=super().stats()
        phase=stats.get("vtResidencyPhase",0)
        if phase==1 and stats["completedSamples"]:
            raise AssertionError(f"Path Tracer accumulated before residency: {stats}")
        if stats.get("completedSamples") and phase==2:
            if not stats["vtRequiredPages"] or stats["vtResidentPages"]!=stats["vtRequiredPages"] or stats["vtReadyMappings"]!=stats["vtRequiredMappings"]:
                raise AssertionError(f"Path Tracer sampled incomplete UDIM data: {stats}")
        value={key:stats[key] for key in ("generation","completedSamples","vtResidencyPhase","vtResidencyGeneration",
            "vtRequiredPages","vtResidentPages","vtLockedTiles","vtRequiredMappings","vtReadyMappings","vtPreparationFrames","vtPreparationMs")}
        if value!=self.last_progress:
            self.progress.append(value); self.last_progress=value
        return stats

    def wait_preparing(self,previous):
        self.view.updateGL()
        while True:
            value=self.stats()
            if value["generation"]>previous and value["vtResidencyPhase"]==1 and value["vtLockedTiles"]:
                return value
            yield

    def image(self,label,previous=0,samples=64):
        return (yield from self.wait_frame(label,previous,samples,require_red=False))

    def run(self):
        model=self.controller._dataModel; stage=model.stage
        root,session=stage.GetRootLayer(),stage.GetSessionLayer()
        source_hash=hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()
        original_session=session.ExportToString(); edit_target=stage.GetEditTarget()
        model.viewSettings.showHUD=False; model.viewSettings.showBBoxes=False
        model.viewSettings.cameraPrim=stage.GetPrimAtPath("/Camera")
        self.controller._mainWindow.resize(1600,1000); self.view.setFixedSize(1280,720)
        yield
        stage.SetEditTarget(session)
        failures=[]
        try:
            # Switch immediately; the first PT render must warm pages outside the
            # screen footprint too (4K tiles, even at a 1280x720 viewport).
            self.launcher.select_mode("pathtraced")
            cold=yield from self.wait_preparing(0)
            api=ResidencyApi(self.launcher.runtime.library)
            api.configure(cold["sessionHandle"],timeout_seconds=0.3)
            model.currentFrame=Usd.TimeCode(model.currentFrame.GetValue()+1)
            self.view.updateGL()
            peak_locks=0
            while True:
                raw=self.view._renderer.GetRenderStats()
                if raw["generation"]>cold["generation"]:
                    peak_locks=max(peak_locks,raw["vtLockedTiles"])
                    if raw["vtResidencyPhase"]==3: break
                    self.stats()
                yield
            if raw["completedSamples"] or "timed out" not in raw["error"] or not peak_locks:
                raise AssertionError(f"Cold timeout did not cover active page locks: {raw}")
            # Failed preparation must promptly release its own locks while
            # retaining error/status data for the owner and the UI.
            while api.status(raw["sessionHandle"])["locked_tiles"]: yield
            failures.append(dict(kind="cold-timeout",peak_locked_tiles=peak_locks,released_locked_tiles=api.status(raw["sessionHandle"])["locked_tiles"],**raw))
            api.configure(raw["sessionHandle"])
            model.currentFrame=Usd.TimeCode(model.currentFrame.GetValue()+1)
            yield from self.image("large-udim-64",raw["generation"])
            first=self.stats()
            if first["vtRequiredPages"]<10000 or first["vtTextures"]!=2:
                raise AssertionError(f"Fixture did not exercise large residency: {first}")
            previous=first["generation"]
            model.currentFrame=Usd.TimeCode(model.currentFrame.GetValue()+1)
            preparing=yield from self.wait_preparing(previous)
            cancelled=preparing["generation"]
            # Cancel during preparation through a real material edit; the next
            # generation must acquire its own locks and start accumulation at zero.
            UsdShade.Shader(stage.GetPrimAtPath("/Looks/UDIM_Normal/Normal")).GetInput("scale").Set(0.9)
            yield from self.image("edited-during-preparation",cancelled)
            for name,budget,timeout,needle in (("budget",1,120,"budget exceeded"),("timeout",0,0.000001,"timed out")):
                before=self.stats()["generation"]
                api.configure(self.stats()["sessionHandle"],budget,timeout)
                model.currentFrame=Usd.TimeCode(model.currentFrame.GetValue()+1)
                self.view.updateGL()
                while True:
                    raw=self.view._renderer.GetRenderStats()
                    if raw["generation"]>before and raw["vtResidencyPhase"]==3: break
                    yield
                if raw["completedSamples"] or needle not in raw["error"]:
                    raise AssertionError(f"Residency failure started PT or lost diagnostics: {raw}")
                failures.append(dict(kind=name,**raw))
                api.configure(raw["sessionHandle"])
                model.currentFrame=Usd.TimeCode(model.currentFrame.GetValue()+1)
                yield from self.image(name+"-recovered",raw["generation"])
            before=self.stats()["generation"]
            self.launcher.select_mode("realtime")
            yield from self.image("realtime-releases-locks",before,samples=0)
            if self.stats()["vtLockedTiles"] or self.stats()["vtResidencyPhase"]:
                raise AssertionError("Realtime retained Path Tracer locks")
            self.switch("HdStormRendererPlugin")
            for _ in range(4): yield
            self.switch(PLUGIN)
            self.launcher.select_mode("pathtraced")
            yield from self.image("recreated-64")
        finally:
            session.ImportFromString(original_session); stage.SetEditTarget(edit_target)
        if hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()!=source_hash: raise AssertionError("Source changed")
        if not any(p["vtResidencyPhase"]==1 and p["vtLockedTiles"] and p["completedSamples"]==0 for p in self.progress):
            raise AssertionError("No asynchronous preparation was observed")
        self.launcher.test_result=dict(residency_verified=True,frames=self.frames,progress=self.progress,
            cancelled_generation=cancelled,failures=failures,source_unchanged=True,same_process=True,
            module_handles=self.launcher.runtime.module_handles())
