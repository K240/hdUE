"""Resolve hdUE wet opinions after USD composition, independently per binding."""
import math
from pxr import Sdf, UsdGeom, UsdShade

PREFIX = "hdue:surface:wet:"
FIELDS = {"enabled": (Sdf.ValueTypeNames.Bool, False), "amount": (Sdf.ValueTypeNames.Float, 1.),
          "roughness": (Sdf.ValueTypeNames.Float, .08), "maskPrimvar": (Sdf.ValueTypeNames.Token, "")}


def settings(prim, time):
    result, sources = {}, {}
    for name, (kind, default) in FIELDS.items():
        current = prim
        while current and not current.IsPseudoRoot():
            attr = current.GetAttribute(PREFIX + name)
            if attr and attr.HasAuthoredValueOpinion():
                if attr.GetTypeName() != kind:
                    raise ValueError(f"{attr.GetPath()}: expected {kind}")
                value = attr.Get(time)
                if value is None:
                    raise ValueError(f"{attr.GetPath()}: no value at this time")
                result[name], sources[name] = value, str(current.GetPath())
                break
            current = current.GetParent()
        else:
            result[name], sources[name] = default, "default"
    for name in ("amount", "roughness"):
        value = float(result[name])
        if not math.isfinite(value):
            raise ValueError(f"{prim.GetPath()}: non-finite wet {name}")
        result[name] = min(1., max(0., value))
    result["maskPrimvar"] = str(result["maskPrimvar"])
    if result["maskPrimvar"].startswith("primvars:"):
        raise ValueError(f"{prim.GetPath()}: use the mask base name without 'primvars:'")
    return result, sources


def validate_mask(mesh, name, time):
    if not name:
        return dict(interpolation="constant", count=1)
    pv = UsdGeom.PrimvarsAPI(mesh).FindPrimvarWithInheritance(name)
    if not pv or pv.GetTypeName() not in (Sdf.ValueTypeNames.Float, Sdf.ValueTypeNames.FloatArray):
        raise ValueError(f"{mesh.GetPath()}: missing float wet mask '{name}'")
    domain = str(pv.GetInterpolation())
    values = pv.ComputeFlattened(time)
    if values is None:
        raise ValueError(f"{pv.GetAttr().GetPath()}: invalid indexed wet mask")
    samples = [values] if pv.GetTypeName() == Sdf.ValueTypeNames.Float else list(values)
    counts = mesh.GetFaceVertexCountsAttr().Get(time) or []
    points = mesh.GetPointsAttr().Get(time) or []
    expected = {"constant": 1, "uniform": len(counts), "vertex": len(points), "varying": len(points),
                "faceVarying": sum(counts)}.get(domain)
    if expected is None or len(samples) != expected or (pv.GetTypeName() == Sdf.ValueTypeNames.Float and domain != "constant"):
        raise ValueError(f"{pv.GetAttr().GetPath()}: invalid {domain} wet mask count ({len(samples)}, expected {expected})")
    if not all(math.isfinite(v) for v in samples):
        raise ValueError(f"{pv.GetAttr().GetPath()}: non-finite wet mask")
    return dict(interpolation=domain, count=len(samples))


def resolve(stage, entries, time):
    bindings, diagnostics = {}, []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        subsets = UsdShade.MaterialBindingAPI(prim).GetMaterialBindSubsets()
        for target in [prim] + [s.GetPrim() for s in subsets]:
            wet, sources = settings(target, time)
            if not wet["enabled"] or wet["amount"] == 0:
                continue
            material, _ = UsdShade.MaterialBindingAPI(target).ComputeBoundMaterial()
            path = str(target.GetPath())
            entry = entries.get(str(material.GetPath())) if material else None
            capability = (entry or {}).get("surface_effects", {}).get("wet", {})
            if capability.get("version") != 1 or not capability.get("supported"):
                reason = capability.get("reason", "prepare/import/cook the MaterialX library with -WetProfile")
                raise ValueError(f"{path}: wet profile unavailable: {reason}")
            for field in ("asset", "preview_asset"):
                if not capability.get(field, "").startswith("/Game/"):
                    raise ValueError(f"{path}: invalid cooked wet {field}")
            mask = validate_mask(mesh, wet["maskPrimvar"], time)
            bindings[path] = dict(asset=capability["asset"], previewAsset=capability["preview_asset"],
                                  amount=wet["amount"], roughness=wet["roughness"], maskPrimvar=wet["maskPrimvar"])
            diagnostics.append(dict(path=path, material=str(material.GetPath()), sources=sources, **mask))
    return bindings, diagnostics
