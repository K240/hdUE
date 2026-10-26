"""Expose hdue:asset on USD Xforms through Hydra's mesh compatibility path.

Only an anonymous session sublayer is changed. The box supplies host bounds and
selection; hdUE instantiates the native asset instead of rendering the box.
"""
import itertools
import json
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom
from asset_proxy import author_proxy


class AssetReferences:
    def __init__(self, controller, catalog=None):
        self.controller = controller
        self.catalog = json.loads(Path(catalog).read_text(encoding="utf-8-sig"))["assets"] if catalog else {}
        self.stage = None
        self.layer = None
        self.previous = None
        self.dirty = True
        self.warnings = []
        self.bounds_seen = set()
        self.api = None
        controller._dataModel.signalStageReplaced.connect(self.invalidate)
        controller._dataModel.signalPrimsChanged.connect(self.invalidate)

    def invalidate(self, *args):
        self.dirty = True

    def sync(self):
        stage = self.controller._dataModel.stage
        if not stage:
            return
        if stage != self.stage:
            self.stage = stage
            self.layer = Sdf.Layer.CreateAnonymous("hdue-native-assets.usda")
            stage.GetSessionLayer().subLayerPaths.insert(0, self.layer.identifier)
            self.previous = None
            self.dirty = True
        if not self.dirty:
            return
        refs = {}
        for prim in stage.Traverse():
            attr = prim.GetAttribute("hdue:asset")
            value = attr.Get() if attr else None
            if value:
                if isinstance(value, Sdf.AssetPath):
                    value = value.path
                if not isinstance(value, str):
                    raise ValueError(f"hdue:asset requires a UE object path string at {prim.GetPath()}")
                refs[str(prim.GetPath())] = (value, prim.IsA(UsdGeom.Mesh))
        units = UsdGeom.GetStageMetersPerUnit(stage) * 100
        axis = str(UsdGeom.GetStageUpAxis(stage))
        current = (refs, units, axis)
        self.warnings = [f"{path}: unavailable source script package {package} (its actors are omitted by cook)"
                         for path, (asset, _) in refs.items()
                         for package in self.catalog.get(asset, {}).get("unavailable_script_packages", [])]
        if current != self.previous:
            self.bounds_seen.clear()
            with Sdf.ChangeBlock():
                self.layer.Clear()
            # Usd authoring requires composition, so keep it outside ChangeBlock.
            with Usd.EditContext(stage, self.layer):
                for path, (asset, is_mesh) in refs.items():
                    author_proxy(stage,path,asset,self.catalog,units,axis,is_mesh)
            self.previous = current
        self.dirty = False

    def refresh_bounds(self, runtime, stats):
        if not self.previous or not self.previous[0] or stats.get("nativeAssetLoading") or stats.get("nativeAssetFailed"):
            return
        session=stats.get("sessionHandle",0)
        if not session or not stats.get('nativeAssetInstances'): return
        if self.api is None:
            from asset_runtime import Assets
            self.api=Assets(runtime)
        refs,units,axis=self.previous
        frame=self.controller._dataModel.currentFrame
        for path,(asset,is_mesh) in refs.items():
            prim=self.stage.GetPrimAtPath(path)
            if not prim: continue
            world=UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(frame)
            visible=UsdGeom.Imageable(prim).ComputeVisibility(frame)
            key=(session,path,asset,str(world),visible)
            if key in self.bounds_seen: continue
            mesh_path=path if is_mesh else path+"/__HdUEAssetProxy"
            native_id=stats.get('nativeAssetIds',{}).get(path)
            if not native_id: continue
            info=self.api.status(session,native_id)
            if info['state']!=1: continue
            self.bounds_seen.add(key)
            if info['bounds_min']==info['bounds_max']: continue
            inverse=world.GetInverse()
            def local(p):
                usd=Gf.Vec3d(*((p[0],-p[1],p[2]) if axis=='Z' else (p[1],p[2],-p[0])))/units
                return Gf.Vec3f(inverse.Transform(usd))
            points=[local(p) for p in itertools.product(*zip(info['bounds_min'],info['bounds_max']))]
            mesh=UsdGeom.Mesh(self.stage.GetPrimAtPath(mesh_path))
            if list(mesh.GetPointsAttr().Get())==points: continue
            with Usd.EditContext(self.stage,self.layer):
                mesh.GetPointsAttr().Set(points)
                mesh.GetExtentAttr().Set([Gf.Vec3f(*(min(p[i] for p in points) for i in range(3))),
                                          Gf.Vec3f(*(max(p[i] for p in points) for i in range(3)))])
