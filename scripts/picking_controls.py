"""On-demand picking of USD and cooked UE geometry, without a render wait."""
from pxr import Gf, Sdf

PLUGIN = "HdUERendererPlugin"


def install_picking(controller):
    view = controller._stageView
    original = view.pick
    last_error = None

    def no_hit():
        return Gf.Vec3d(0), Gf.Vec3d(0), Sdf.Path.emptyPath, -1, Sdf.Path.emptyPath, -1

    def pick(frustum):
        nonlocal last_error
        if view.GetCurrentRendererId() != PLUGIN:
            return original(frustum)
        renderer = view._renderer
        stage = controller._dataModel.stage
        if renderer is None or stage is None:
            return no_hit()
        # Resolve nearest to the cursor center, with a small subpixel footprint
        # matching the narrowed frustum supplied by usdview.
        samples = [(0, 0), (-.65, 0), (.65, 0), (0, -.65), (0, .65),
                   (-.65, -.65), (.65, -.65), (-.65, .65), (.65, .65)]
        forward = frustum.ComputeViewDirection().GetNormalized()
        for x, y in samples:
            ray = frustum.ComputePickRay(Gf.Vec2d(x, y))
            direction = ray.direction.GetNormalized()
            cosine = Gf.Dot(direction, forward)
            if cosine <= 0:
                continue
            offset = Gf.Dot(ray.startPoint - frustum.position, forward)
            minimum = max(0.0, float((frustum.nearFar.GetMin() - offset) / cosine))
            maximum = float((frustum.nearFar.GetMax() - offset) / cosine)
            if not renderer.InvokeRendererCommand("hdue:pickRay", {
                    "origin": ray.startPoint, "direction": direction,
                    "minimum": minimum, "maximum": maximum}):
                error = "hdUE picking is unavailable; rebuild the host plugin"
                if error != last_error:
                    controller.statusMessage(error)
                    last_error = error
                return no_hit()
            result = renderer.GetRenderStats().get("pick", {})
            error = result.get("error", "")
            if error:
                if error != last_error:
                    controller.statusMessage(error)
                    last_error = error
                return no_hit()
            if last_error:
                controller.statusMessage("")
            last_error = None
            if result.get("hit"):
                path = Sdf.Path(result["path"])
                if stage.GetPrimAtPath(path):
                    point = Gf.Vec3d(result["px"], result["py"], result["pz"])
                    normal = Gf.Vec3d(result["nx"], result["ny"], result["nz"])
                    return point, normal, path, -1, Sdf.Path.emptyPath, -1
        return no_hit()

    # Use the normal usdview selection/signals/tree path after returning a hit.
    # This instance hook leaves the installed OpenUSD modules unchanged.
    view.pick = pick
