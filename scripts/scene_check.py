"""Exercise viewer lighting on an unlit USD, without saving source edits."""
import hashlib
import time
from pathlib import Path

import numpy as np
from pxr import UsdGeom, UsdLux
from pxr.Usdviewq.qt import QtGui
from usdview_test import SmokeTest, PLUGIN


class SceneCheck(SmokeTest):
    def wait_image(self, label, previous=0, viewer=2, scene=0, samples=0, lit=True):
        start = time.perf_counter()
        self.view.updateGL()
        while True:
            stats = self.stats()
            if (stats.get("generation", 0) > previous and stats.get("frameId", 0)
                    and self.view.IsRendererConverged()):
                break
            yield
        if stats.get("meshes", 0) < 1 or stats.get("completedSamples", 0) < samples:
            raise AssertionError(f"{label}: incomplete render: {stats}")
        if (stats.get("viewerLights"), stats.get("sceneLights")) != (viewer, scene):
            raise AssertionError(f"{label}: wrong light selection: {stats}")
        yield
        image = self.view.grabFrameBuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
        raw = np.frombuffer(image.bits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
        rgb = raw[:, :image.width()*4].reshape(image.height(), image.width(), 4)[..., :3].copy()
        visible = np.max(rgb, axis=2) > 10
        white = np.all(rgb > 245, axis=2)
        white_fraction = float(white.sum()/max(1, visible.sum()))
        if lit and (visible.mean() < 0.04 or white_fraction > 0.15):
            raise AssertionError(f"{label}: image is black or overexposed: {visible.mean()}, {white_fraction}")
        if not image.save(str(self.output/(label + ".png"))):
            raise RuntimeError("Cannot save scene-check image")
        self.frames.append(dict(label=label, seconds=time.perf_counter()-start,
            mean_rgb=float(rgb.mean()), lit_fraction=float(visible.mean()), white_fraction=white_fraction, **stats))
        print(f"scene-check {label}: {self.frames[-1]}", flush=True)
        return rgb

    def run(self):
        model = self.controller._dataModel
        stage = model.stage
        root, session = stage.GetRootLayer(), stage.GetSessionLayer()
        source = Path(root.realPath)
        before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        root_text, session_text = root.ExportToString(), session.ExportToString()
        edit_target = stage.GetEditTarget()
        model.viewSettings.showHUD = False
        model.viewSettings.showBBoxes = False
        self.controller._mainWindow.resize(1600, 1000)
        yield
        first = yield from self.wait_image("01-auto-realtime")
        if self.stats().get('nativeAssetInstances') and self.launcher.options.camera.IsAbsolutePath():
            camera=model.viewSettings.cameraPrim
            if not camera or camera.GetPath()!=self.launcher.options.camera:
                raise AssertionError('Native asset startup lost the requested scene camera')
        self.launcher.auto_light_action.setChecked(False)
        dark = yield from self.wait_image("02-auto-off", self.frames[-1]["generation"], viewer=0, lit=False)
        if dark.mean() >= first.mean()*0.02:
            raise AssertionError("Disabling auto lighting did not restore the unlit scene")
        self.launcher.auto_light_action.setChecked(True)
        yield from self.wait_image("03-auto-restored", self.frames[-1]["generation"])
        # Stock Lights > Enable Default Camera Light must be calibrated and
        # suppress automatic lights, even though Hdx reports intensity=15000.
        model.viewSettings.ambientLightOnly = True
        camera_lit = yield from self.wait_image("04-camera-light", self.frames[-1]["generation"], viewer=1)
        self.view.SetRendererSetting("viewerLightIntensity", 1.0)
        dim = yield from self.wait_image("04b-dim-camera-light", self.frames[-1]["generation"], viewer=1)
        if dim.mean() >= camera_lit.mean()*0.8:
            raise AssertionError("Viewer light intensity did not change the image")
        self.view.SetRendererSetting("viewerLightIntensity", 4.0)
        yield from self.wait_image("04c-restored-camera-light", self.frames[-1]["generation"], viewer=1)
        submissions = self.stats()["sceneSubmissions"]
        if self.view.getActiveSceneCamera() is not None:
            self.view.switchToFreeCamera()
        model.viewSettings.freeCamera.Tumble(8, 3)
        yield from self.wait_image("05-camera-moved", self.frames[-1]["generation"], viewer=1)
        if self.stats()["sceneSubmissions"] != submissions:
            raise AssertionError("Moving the camera light resubmitted the scene")
        model.viewSettings.ambientLightOnly = False
        yield from self.wait_image("06-camera-disabled", self.frames[-1]["generation"])
        # Add/edit/delete a light only in the anonymous session layer. This
        # proves live changes update automatic lighting without scanning USD
        # on every Qt tick or changing the user's root layer.
        try:
            stage.SetEditTarget(session)
            light = UsdLux.DistantLight.Define(stage, "/HdUEViewerLightTest")
            light.CreateIntensityAttr(4)
            light.AddTransformOp().Set(self.view.resolveCamera()[0].transform)
            yield from self.wait_image("07-scene-light", self.frames[-1]["generation"], viewer=0, scene=1)
            model.viewSettings.enableSceneLights = False
            yield from self.wait_image("07b-scene-lights-disabled", self.frames[-1]["generation"])
            model.viewSettings.enableSceneLights = True
            yield from self.wait_image("07c-scene-lights-enabled", self.frames[-1]["generation"], viewer=0, scene=1)
            visibility = UsdGeom.Imageable(light).CreateVisibilityAttr()
            visibility.Set(UsdGeom.Tokens.invisible)
            yield from self.wait_image("08-hidden-light", self.frames[-1]["generation"])
            visibility.Set(UsdGeom.Tokens.inherited)
            light.GetIntensityAttr().Set(0)
            yield from self.wait_image("09-zero-light", self.frames[-1]["generation"])
            light.GetIntensityAttr().Set(4)
            yield from self.wait_image("10-restored-light", self.frames[-1]["generation"], viewer=0, scene=1)
            stage.RemovePrim(light.GetPath())
            yield from self.wait_image("11-removed-light", self.frames[-1]["generation"])
        finally:
            session.ImportFromString(session_text)
            stage.SetEditTarget(edit_target)
        self.launcher.select_mode("pathtraced")
        yield from self.wait_image("12-pathtraced", self.frames[-1]["generation"], samples=64)
        self.launcher.select_mode("realtime")
        yield from self.wait_image("13-realtime-restored", self.frames[-1]["generation"])
        self.switch("HdStormRendererPlugin")
        for _ in range(5):
            yield
        self.switch(PLUGIN)
        yield from self.wait_image("14-recreated")
        if (root.ExportToString() != root_text or session.ExportToString() != session_text
                or hashlib.sha256(source.read_bytes()).hexdigest() != before_hash):
            raise AssertionError("Viewer lighting modified the USD source / session layer")
        self.controller._mainWindow.grab().save(str(self.output/"scene-window.png"))
        modules = self.launcher.runtime.module_handles()
        if not all(modules.values()):
            raise AssertionError("Hydra and UE are not loaded in this process")
        self.launcher.test_result = dict(frames=self.frames, module_handles=modules, same_process=True,
            source=str(source), source_sha256=before_hash, usd_unchanged=True,
            camera_move_scene_submissions=0, checks="viewer-lighting")
