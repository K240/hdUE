"""Measure actual Alt+drag handling, image delivery and settling in usdview."""
import math
import time

import numpy as np
from pxr import Usd
from pxr.Usdviewq.qt import QtCore, QtGui
from usdview_test import SmokeTest


class CameraBenchmark(SmokeTest):
    def __init__(self, launcher, output):
        super().__init__(launcher, output)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)

    def start(self):
        self.steps = self.run()
        self.timer.start(16)

    def mouse(self, kind, x, y, button, buttons):
        point = QtCore.QPointF(x, y)
        event = QtGui.QMouseEvent(kind, point, point, button, buttons, QtCore.Qt.AltModifier)
        if kind == QtCore.QEvent.MouseButtonPress:
            self.view.mousePressEvent(event)
        elif kind == QtCore.QEvent.MouseButtonRelease:
            self.view.mouseReleaseEvent(event)
        else:
            self.view.mouseMoveEvent(event)

    def run(self):
        model = self.controller._dataModel
        model.viewSettings.showHUD = False
        model.viewSettings.showBBoxes = False
        model.viewSettings.cameraPrim = model.stage.GetPrimAtPath("/Camera")
        model.currentFrame = Usd.TimeCode(0)
        self.controller._mainWindow.resize(1800, 1100)
        self.view.setFixedSize(960, 540)
        yield
        yield from self.wait_frame("camera-warmup")
        press_start = time.perf_counter()
        self.mouse(QtCore.QEvent.MouseButtonPress, 400, 250, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
        press_ms = (time.perf_counter() - press_start) * 1000
        # Render the initial transition to the free camera before measuring.
        yield
        while not self.view.IsRendererConverged():
            yield
        before = dict(self.stats())
        stamps, handlers = [], []
        start = time.perf_counter()
        while time.perf_counter() - start < 3:
            now = time.perf_counter()
            phase = (now - start) * 2 * math.pi / 3
            x, y = 400 + 100 * math.sin(phase), 250 + 25 * math.sin(phase * 2)
            handler_start = time.perf_counter()
            self.mouse(QtCore.QEvent.MouseMove, x, y, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
            handlers.append((time.perf_counter() - handler_start) * 1000)
            stamps.append(now)
            self.stats()
            yield
        end = time.perf_counter()
        after = dict(self.stats())
        self.mouse(QtCore.QEvent.MouseButtonRelease, x, y, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)
        released = time.perf_counter()
        first_frame_ms = None
        while not self.view.IsRendererConverged():
            if first_frame_ms is None and self.stats()["publishedFrames"] > after["publishedFrames"]:
                first_frame_ms = (time.perf_counter() - released) * 1000
            yield
        settled_ms = (time.perf_counter() - released) * 1000
        first_frame_ms = settled_ms if first_frame_ms is None else first_frame_ms
        yield from self.wait_frame("camera-settled")
        gaps = np.diff(stamps) * 1000
        requests = after["generation"] - before["generation"]
        deliveries = after["publishedFrames"] - before["publishedFrames"]
        if deliveries < 3:
            raise AssertionError(f"Continuous camera motion starved image delivery: {deliveries} images in {end-start:.3f}s")
        metrics = {
            "duration_seconds": end - start, "width": self.view.width(), "height": self.view.height(),
            "input_events": len(stamps), "input_hz": len(stamps) / (end-start),
            "input_gap_p50_ms": float(np.percentile(gaps, 50)),
            "input_gap_p95_ms": float(np.percentile(gaps, 95)), "input_gap_max_ms": float(max(gaps)),
            "handler_p95_ms": float(np.percentile(handlers, 95)), "gesture_start_ms": press_ms,
            "camera_requests": requests, "published_during_motion": deliveries,
            "displayed_fps_during_motion": deliveries / (end-start),
            "request_cpu_mean_ms": (after["requestCpuMsTotal"]-before["requestCpuMsTotal"]) / max(1, requests),
            "scene_batches_during_motion": after["sceneSubmissions"]-before["sceneSubmissions"],
            "coalesced_camera_updates": after["coalescedCameraUpdates"]-before["coalescedCameraUpdates"],
            "release_to_first_frame_ms": first_frame_ms, "release_to_converged_ms": settled_ms,
        }
        modules = self.launcher.runtime.module_handles()
        if not all(modules.values()) or not requests:
            raise AssertionError("Camera benchmark did not use the same-process UE renderer")
        self.launcher.test_result = {"camera_benchmark": metrics, "frames": self.frames,
            "same_process": True, "module_handles": modules, "usd_version": list(Usd.GetVersion())}
        print(f"Camera benchmark: {metrics}", flush=True)
