"""Real GUI integration test, driven by Qt events (no second UE process)."""
import time
import traceback

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom
from pxr.Usdviewq.common import PickModes
from pxr.Usdviewq.qt import QtCore, QtGui, QtWidgets

PLUGIN = "HdUERendererPlugin"


class SmokeTest:
    def __init__(self, launcher, output):
        self.launcher = launcher
        self.output = output
        self.controller = launcher.controller
        self.view = self.controller._stageView
        self.frames = []
        self.picks = []
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.advance)
        self.deadline = time.monotonic() + 300

    def start(self):
        self.steps = self.run()
        self.timer.start(25)

    def advance(self):
        try:
            if time.monotonic() > self.deadline:
                raise TimeoutError("usdview integration timed out")
            if self.launcher.failure:
                raise RuntimeError(self.launcher.failure)
            next(self.steps)
        except StopIteration:
            self.timer.stop()
            self.controller._mainWindow.close()
        except BaseException:
            self.timer.stop()
            self.launcher.failure = traceback.format_exc()
            self.controller._mainWindow.close()

    def stats(self):
        renderer = self.view._renderer
        stats = renderer.GetRenderStats() if renderer else {}
        if stats.get("error"):
            raise RuntimeError(stats["error"])
        return stats

    def wait_frame(self, label, previous_generation=0, samples=0, require_red=True):
        start = time.monotonic()
        self.view.updateGL()
        while True:
            stats = self.stats()
            if (stats.get("generation", 0) > previous_generation and
                    stats.get("frameId", 0) and self.view.IsRendererConverged()):
                break
            yield
        if stats.get("completedSamples", 0) < samples:
            raise AssertionError(f"{label}: premature convergence: {stats}")
        if stats.get("meshes", 0) < 2 or stats.get("lights", 0) < 1:
            raise AssertionError(f"{label}: missing USD scene: {stats}")
        print(f"usdview {label}: {stats}", flush=True)
        # Let the final AOV upload/presentation finish; no nested processEvents.
        yield
        image = self.view.grabFrameBuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
        pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
        rgb = pixels[:, :image.width() * 4].reshape(image.height(), image.width(), 4)[..., :3]
        # A red material region proves the screenshot contains rendered USD,
        # rather than only the gray viewport / HUD / selection rectangle.
        red = (rgb[..., 0] > 35) & (rgb[..., 0] > rgb[..., 1] * 1.5) & (rgb[..., 0] > rgb[..., 2] * 1.5)
        if require_red and np.count_nonzero(red) < 50:
            image.save(str(self.output / (label + "-failed.png")))
            raise AssertionError(f"{label}: color AOV not visible in stock usdview")
        if not image.save(str(self.output / (label + ".png"))):
            raise RuntimeError("Cannot save viewport screenshot")
        self.frames.append(dict(label=label, seconds=time.monotonic()-start,
            width=image.width(), height=image.height(), red_pixels=int(np.count_nonzero(red)), **stats))
        return rgb.copy()

    def switch(self, plugin):
        self.view.makeCurrent()
        try:
            self.controller._rendererPluginChanged(plugin)
        finally:
            self.view.doneCurrent()
        if self.view.GetCurrentRendererId() != plugin:
            raise AssertionError(f"Renderer switch failed: {plugin}")

    def drag_camera(self, button, dx, dy, modifiers=QtCore.Qt.AltModifier):
        for kind, point, changed, held in (
            (QtCore.QEvent.MouseButtonPress, QtCore.QPointF(200, 150), button, button),
            (QtCore.QEvent.MouseMove, QtCore.QPointF(200 + dx, 150 + dy), QtCore.Qt.NoButton, button),
            (QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(200 + dx, 150 + dy), button, QtCore.Qt.NoButton)):
            event = QtGui.QMouseEvent(kind, point, point, changed, held, modifiers)
            QtWidgets.QApplication.sendEvent(self.view, event)

    def camera_input(self, kind):
        camera = self.controller._dataModel.viewSettings.freeCamera
        before = self.view.resolveCamera()[0].transform
        center, distance = Gf.Vec3d(camera.center), camera.dist
        if kind == "pan":
            self.drag_camera(QtCore.Qt.MiddleButton, 35, 12)
        elif kind == "pan-without-middle":
            self.drag_camera(QtCore.Qt.LeftButton, -20, -8,
                             QtCore.Qt.AltModifier | QtCore.Qt.ControlModifier)
        elif kind == "dolly":
            self.drag_camera(QtCore.Qt.RightButton, 30, 10)
        elif kind == "wheel":
            point = QtCore.QPointF(200, 150)
            event = QtGui.QWheelEvent(point, point, QtCore.QPoint(), QtCore.QPoint(0, 120),
                QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
            QtWidgets.QApplication.sendEvent(self.view, event)
        after = self.view.resolveCamera()[0].transform
        if not np.allclose(np.array(before)[:3, :3], np.array(after)[:3, :3], atol=1e-6):
            raise AssertionError(f"{kind}: rotated the camera instead of translating it")
        if (after.ExtractTranslation() - before.ExtractTranslation()).GetLength() < 1e-4:
            raise AssertionError(f"{kind}: camera did not move")
        if kind.startswith("pan"):
            if (camera.center - center).GetLength() < 1e-4 or abs(camera.dist - distance) > 1e-5:
                raise AssertionError(f"{kind}: pan did not translate the camera's center")
        elif camera.dist >= distance or (camera.center - center).GetLength() > 1e-4:
            raise AssertionError(f"{kind}: dolly did not move towards the camera's center")

    def click_scene(self, x, y, expected):
        # Coarse diagnostic for the complete Qt selection event, not a BVH benchmark.
        start = time.monotonic()
        before = self.stats()
        scale = self.view.devicePixelRatioF()
        point = QtCore.QPointF(float(x) / scale, float(y) / scale)
        for kind, held in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                           (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
            event = QtGui.QMouseEvent(kind, point, point, QtCore.Qt.LeftButton, held, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(self.view, event)
        selected = self.controller._dataModel.selection.getPrimPaths()
        target = Sdf.Path(expected) if expected else Sdf.Path.absoluteRootPath
        if selected != [target]:
            raise AssertionError(f"Click should select {target}, got {selected}; {self.stats().get('pick')}")
        after = self.stats()
        if (after["generation"], after["completedSamples"]) != (before["generation"], before["completedSamples"]):
            raise AssertionError("Picking restarted UE rendering / Path Tracer accumulation")
        self.picks.append({"expected": str(target), "selected": [str(p) for p in selected],
                           "query": after.get("pick", {}), "milliseconds": (time.monotonic()-start)*1000,
                           "generation": after["generation"], "completedSamples": after["completedSamples"]})

    def run(self):
        model = self.controller._dataModel
        model.viewSettings.showHUD = False
        model.viewSettings.showBBoxes = False
        model.viewSettings.cameraPrim = model.stage.GetPrimAtPath("/Camera")
        model.currentFrame = Usd.TimeCode(0)
        self.controller._mainWindow.resize(1100, 800)
        yield
        if self.view.GetCurrentRendererId() != PLUGIN:
            raise AssertionError("hdUE was not selected by stock usdview")
        yield from self.wait_frame("01-realtime")
        self.launcher.select_mode("pathtraced")
        original = yield from self.wait_frame("02-pathtraced", self.frames[-1]["generation"], 64)
        previous = self.frames[-1]["generation"]
        # Session-layer edits must remain unsaved in this smoke test.
        model.stage.SetEditTarget(model.stage.GetSessionLayer())
        cube = UsdGeom.Xformable(model.stage.GetPrimAtPath("/World/Cube"))
        # A stronger layer's time samples replace the weaker sample set.
        # Author both keys so frame 1 remains a distinct pose.
        cube.GetOrderedXformOps()[0].Set(Gf.Vec3d(-50, 0, 0), Usd.TimeCode(1))
        cube.GetOrderedXformOps()[0].Set(Gf.Vec3d(100, 0, 0), model.currentFrame)
        edited = yield from self.wait_frame("03-edited-pathtraced", previous, 64)
        if original.shape == edited.shape and np.mean(np.abs(original.astype(float)-edited)) < 0.1:
            raise AssertionError("USD edit did not change the displayed AOV")
        previous = self.frames[-1]["generation"]
        model.currentFrame = Usd.TimeCode(1)
        timed = yield from self.wait_frame("04-time", previous, 64)
        if np.mean(np.abs(edited.astype(float)-timed)) < 0.1:
            raise AssertionError("Time change did not update the displayed USD scene")
        previous = self.frames[-1]["generation"]
        self.controller._mainWindow.resize(1250, 900)
        yield from self.wait_frame("05-resized", previous, 64)
        if (self.frames[-1]["width"], self.frames[-1]["height"]) == (self.frames[-2]["width"], self.frames[-2]["height"]):
            raise AssertionError("Viewport did not resize")
        # Exercise stock Renderer menu switching twice. UE remains initialized.
        for cycle in range(2):
            self.switch("HdStormRendererPlugin")
            for _ in range(5):
                yield
            self.switch(PLUGIN)
            yield from self.wait_frame(f"06-recreated-{cycle}", samples=64)
            if self.launcher.runtime.status().state != 2:
                raise AssertionError("Renderer switch stopped UE")
        # Stage replacement through stock close/open paths must create a new
        # delegate, while preserving the process-scoped UELibrary runtime.
        filename = model.stage.GetRootLayer().realPath
        self.controller._reopenStage()
        yield
        model.viewSettings.cameraPrim = model.stage.GetPrimAtPath("/Camera")
        yield from self.wait_frame("07-reopened", samples=64)
        if model.stage.GetRootLayer().realPath != filename:
            raise AssertionError("Unexpected stage")
        previous = self.frames[-1]["generation"]
        scene_camera = UsdGeom.Camera(model.stage.GetPrimAtPath("/Camera"))
        authored_focus = scene_camera.GetFocusDistanceAttr().Get()
        scene_pose = self.view.resolveCamera()[0].transform
        self.controller._stageView.switchToFreeCamera()
        free_pose = self.view.resolveCamera()[0].transform
        if model.viewSettings.freeCamera.dist <= 0 or not np.allclose(np.array(scene_pose), np.array(free_pose), atol=1e-5):
            raise AssertionError("Free camera needs a positive navigation distance without changing its pose")
        if scene_camera.GetFocusDistanceAttr().Get() != authored_focus:
            raise AssertionError("Navigation modified the USD camera's focus distance")
        yield from self.wait_frame("08-free-camera", previous, 64)
        previous = self.frames[-1]["generation"]
        model.viewSettings.freeCamera.Tumble(12, 5)
        self.view.updateGL()
        previous_image = yield from self.wait_frame("09-tumbled-camera", previous, 64)
        for label, kind in (("10-pan", "pan"), ("11-dolly", "dolly"),
                            ("12-wheel", "wheel"), ("13-pan-without-middle", "pan-without-middle")):
            previous = self.frames[-1]["generation"]
            self.camera_input(kind)
            image = yield from self.wait_frame(label, previous, 64)
            if np.mean(np.abs(image.astype(float) - previous_image)) < 0.1:
                raise AssertionError(f"{kind}: camera moved but the displayed AOV did not change")
            previous_image = image
        model.viewSettings.cameraPrim = model.stage.GetPrimAtPath("/Camera")
        model.viewSettings.pickMode = PickModes.PRIMS
        image = yield from self.wait_frame("14-pick-camera", self.frames[-1]["generation"], 64)
        red = (image[..., 0] > 35) & (image[..., 0] > image[..., 1] * 1.5) & (image[..., 0] > image[..., 2] * 1.5)
        ys, xs = np.where(red)
        x, y = float(np.median(xs)), float(np.median(ys))
        self.click_scene(x, y, "/World/Cube")
        yield from self.wait_frame("15-picked", samples=64)
        if not self.controller._mainWindow.grab().save(str(self.output / "picked-window.png")):
            raise RuntimeError("Cannot save picked usdview window")
        model.stage.SetEditTarget(model.stage.GetSessionLayer())
        visibility = UsdGeom.Imageable(model.stage.GetPrimAtPath("/World/Cube")).GetVisibilityAttr()
        previous = self.frames[-1]["generation"]
        visibility.Set(UsdGeom.Tokens.invisible)
        yield from self.wait_frame("16-hidden-picked", previous, 64, require_red=False)
        self.click_scene(x, y, "/World/Ground")
        previous = self.frames[-1]["generation"]
        visibility.Set(UsdGeom.Tokens.inherited)
        yield from self.wait_frame("17-restored", previous, 64)
        previous = self.frames[-1]["generation"]
        self.view.SetRendererSetting("nativeMeshAssets", {"/World/Cube": "/Game/HdUE/SM_Nanite.SM_Nanite"})
        native_image = yield from self.wait_frame("18-native-assets", previous, 64)
        def color_point(pixels, channel=0):
            mask = (pixels[..., channel] > 35)
            for other in range(3):
                if other != channel:
                    mask &= pixels[..., channel] > pixels[..., other] * 1.5
            ys, xs = np.where(mask)
            if len(xs) < 50:
                raise AssertionError("Missing material region for native picking")
            return (float(xs[len(xs)//2]), float(ys[len(ys)//2])), mask

        (native_x, native_y), native_red = color_point(native_image)
        self.click_scene(native_x, native_y, "/World/Cube")
        query = self.stats()["pick"]
        if query.get("geometry") != "ue-lod0" or not query.get("naniteFallback") or query["pz"] > 50.01:
            raise AssertionError(f"Native picking did not use cooked geometry: {query}")
        # The USD proxy extends to z=100; the cooked cube only to z=50.
        # Click safely inside the proxy-only area: the floor must win.
        mismatch = red & ~native_red
        for axis in (0, 1):
            for offset in (-4, 4):
                mismatch &= np.roll(red, offset, axis) & ~np.roll(native_red, offset, axis)
        ys, xs = np.where(mismatch)
        if len(xs) < 20:
            raise AssertionError("Missing proxy/native silhouette difference")
        self.click_scene(float(xs[len(xs)//2]), float(ys[len(ys)//2]), "/World/Ground")
        previous = self.frames[-1]["generation"]
        visibility.Set(UsdGeom.Tokens.invisible)
        yield from self.wait_frame("19-hidden-native", previous, 64, require_red=False)
        self.click_scene(native_x, native_y, "/World/Ground")
        previous = self.frames[-1]["generation"]
        visibility.Set(UsdGeom.Tokens.inherited)
        UsdGeom.Xformable(model.stage.GetPrimAtPath("/World/Cube")).GetOrderedXformOps()[0].Set(
            Gf.Vec3d(100, 30, 0), model.currentFrame)
        moved = yield from self.wait_frame("20-moved-native", previous, 64)
        moved_point, _ = color_point(moved)
        self.click_scene(*moved_point, "/World/Cube")
        previous = self.frames[-1]["generation"]
        self.view.SetRendererSetting("nativeMaterialAssets", {"/Looks/Red": "/Game/HdUE/M_Native.M_Native"})
        blue = yield from self.wait_frame("21-native-material", previous, 64, require_red=False)
        blue_point, _ = color_point(blue, 2)
        self.click_scene(*blue_point, "/World/Cube")
        previous = self.frames[-1]["generation"]
        self.view.SetRendererSetting("nativeMeshAssets", {})
        blue = yield from self.wait_frame("22-generic-native-material", previous, 64, require_red=False)
        blue_point, _ = color_point(blue, 2)
        self.click_scene(*blue_point, "/World/Cube")
        if self.stats()["pick"].get("geometry") != "usd":
            raise AssertionError("Generic geometry with native material used the wrong pick path")
        previous = self.frames[-1]["generation"]
        self.view.SetRendererSetting("nativeMaterialAssets", {})
        restored = yield from self.wait_frame("23-generic-restored", previous, 64)
        restored_point, _ = color_point(restored)
        self.click_scene(*restored_point, "/World/Cube")
        self.click_scene(10, 10, None)
        if not self.controller._mainWindow.grab().save(str(self.output / "usdview-window.png")):
            raise RuntimeError("Cannot save usdview window screenshot")
        modules = self.launcher.runtime.module_handles()
        if not all(modules.values()):
            raise AssertionError(f"UE / Hydra DLL missing from the GUI process: {modules}")
        self.launcher.test_result = {"frames": self.frames, "picks": self.picks, "module_handles": modules,
            "usd_version": list(Usd.GetVersion()), "same_process": all(modules.values())}
