"""Keep usdview navigation usable when a USD camera has no focus distance."""
import math

from pxr import Gf, UsdGeom


def ensure_navigation_distance(view):
    model = view._dataModel
    camera = model.viewSettings.freeCamera
    if camera is None or (math.isfinite(camera.dist) and camera.dist > 0):
        return
    # FromGfCamera copies focusDistance into dist and the navigation scale.
    # USD's default focusDistance is zero. Preserve the current pose and lens,
    # and choose a pivot in front of it using the selected geometry's bounds.
    camera._pushToCameraTransform()
    frustum = camera._camera.frustum
    position = frustum.position
    direction = frustum.ComputeViewDirection().GetNormalized()
    near = max(float(camera._camera.clippingRange.GetMin()), 0.0)
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(model.stage) if model.stage else 0.01
    fallback = 1.0 / meters_per_unit if math.isfinite(meters_per_unit) and meters_per_unit > 0 else 100.0
    distance = max(near * 10, fallback)
    bounds = view.getSelectionBBox().ComputeAlignedRange()
    if bounds.IsEmpty() and model.stage:
        bounds = model.computeWorldBound(model.stage.GetPseudoRoot()).ComputeAlignedRange()
    if not bounds.IsEmpty():
        projected = Gf.Dot(bounds.GetMidpoint() - position, direction)
        radius = bounds.GetSize().GetLength() * 0.5
        if math.isfinite(projected) and projected > max(near, 0):
            distance = projected
        elif math.isfinite(radius) and radius > 0:
            distance = max(near * 10, radius)
    # Same FreeCamera instance preserves clipping overrides and signal wiring.
    # Its inverse transform decomposition updates dist, center and _selSize
    # together, so both truck and dolly use a nonzero navigation scale.
    camera._camera.focusDistance = distance
    camera._pullFromCameraTransform()
    camera.signalFrustumChanged.emit()


def install_camera_controls(view):
    original = view.switchToFreeCamera

    def switch_to_free_camera(*args, **kwargs):
        original(*args, **kwargs)
        ensure_navigation_distance(view)

    view.switchToFreeCamera = switch_to_free_camera
    if view.getActiveSceneCamera() is None:
        ensure_navigation_distance(view)
