"""Bind external or prepared inline MaterialX to cooked Substrate materials."""
import hashlib
import json
from pathlib import Path

from pxr import Gf, Sdf, UsdShade
from usd_materialx_graph import graph_data, graph_signature, rebase_graph
from materialx_textures import validate_texture_sets


class MaterialXLibrary:
    def __init__(self, file, controller):
        self.controller = controller
        library = json.loads(Path(file).read_text(encoding="utf-8-sig"))
        if library.get("version") != 1:
            raise ValueError("Unsupported MaterialX library version")
        validate_texture_sets(library.get("texture_sets",[]))
        for name, expected in library["dependencies"].items():
            path = Path(name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"MaterialX source changed; import/cook again: {path}")
        self.materials = {(str(Path(p["source"]).resolve()).casefold(), p["material"]): p for p in library["materials"]}
        for entry in library['materials']:
            entry['_hdue_identity']=hashlib.sha256(json.dumps(dict(asset=entry['asset'],model=entry['model'],
                dependencies=sorted(library['dependencies'].values())),sort_keys=True).encode()).hexdigest()
        self.inline = {(str(Path(p["usd_stage"]).resolve()).casefold(),p["usd_material"]):p for p in library["materials"] if "usd_stage" in p}
        self.bound_entries = {}
        self.preparation_error = None
        self.owned = set()
        self.dirty = True
        self.last_renderer = None
        self.last_time = None
        self.bindings = []
        model = controller._dataModel
        model.signalStageReplaced.connect(self.invalidate)
        model.signalPrimsChanged.connect(self.invalidate)

    def invalidate(self, *args):
        self.dirty = True

    def inline_binding(self, prim, stage, time):
        path=str(prim.GetPath())
        stage_file=str(Path(stage.GetRootLayer().realPath).resolve()).casefold()
        exact=self.inline.get((stage_file,path))
        authored={str(spec.path.StripAllVariantSelections()) for spec in prim.GetPrimStack()}
        candidates=[exact] if exact else [e for e in self.inline.values() if e['usd_material'] in authored]
        if not candidates:
            names={prim.GetName()} | {spec.path.name for spec in prim.GetPrimStack()}
            candidates=[e for e in self.inline.values() if Sdf.Path(e['usd_material']).name in names]
        if not candidates:
            return None,[]
        graph=graph_data(UsdShade.Material(prim),time)
        matches=[]
        for entry in candidates:
            rebased=rebase_graph(graph,path,entry['usd_material'])
            if graph_signature(rebased,[p['attribute'] for p in entry['usd_bindings']])==entry['usd_graph_signature']:
                matches.append(entry)
        if len(matches)!=1:
            raise ValueError('MaterialX graph/static input changed or ambiguous; prepare/import/cook again: '+path)
        entry=matches[0]
        values=[]
        for binding in entry['usd_bindings']:
            attribute=path+binding['attribute'][len(entry['usd_material']):]
            attr=stage.GetAttributeAtPath(attribute)
            values.append((binding,attr.Get(time) if attr else None))
        return entry,values

    def sync(self):
        try:
            self._sync()
            self.preparation_error=None
            return True
        except ValueError as error:
            self.preparation_error=str(error)
            return False

    def _sync(self):
        model = self.controller._dataModel
        view = self.controller._stageView
        if not model.stage or view._renderer is None:
            return
        if model.currentFrame != self.last_time or id(view._renderer) != self.last_renderer:
            self.dirty = True
        if not self.dirty:
            return
        asset_map = dict(view.GetRendererSetting("nativeMaterialAssets") or {})
        overrides = dict(view.GetRendererSetting("nativeMaterialParameters") or {})
        for path in self.owned:
            asset_map.pop(path, None)
            overrides.pop(path, None)
        owned, bindings, bound_entries = set(), [], {}
        for prim in model.stage.Traverse():
            if not prim.IsA(UsdShade.Material):
                continue
            output = UsdShade.Material(prim).GetSurfaceOutput("mtlx")
            connection = output.GetConnectedSource() if output else None
            if not connection:
                continue
            path = str(prim.GetPath())
            entry,values=self.inline_binding(prim,model.stage,model.currentFrame)
            if not entry:
                shader = UsdShade.Shader(connection[0].GetPrim())
                source = shader.GetPrim().GetAttribute("info:mtlx:sourceAsset").Get()
                name = shader.GetPrim().GetAttribute("info:mtlx:sourceAsset:subIdentifier").Get()
                if not isinstance(source, Sdf.AssetPath) or not name:
                    raise ValueError(f"Inline MaterialX needs Import-UsdMaterialX.ps1 preparation: {path}")
                file = str(Path(source.resolvedPath or source.path).resolve()).casefold()
                entry = self.materials.get((file, str(name)))
                if entry is None:
                    raise ValueError(f"MaterialX material is absent from the cooked library: {source.path} / {name}")
                values=[]
                for name,p in entry["bindings"].items():
                    source_input=shader.GetInput(name)
                    values.append((p,source_input.Get(model.currentFrame) if source_input else None))
            asset_map[path] = entry["asset"]
            fields = {}
            for p,value in values:
                if value is None:
                    continue
                if p["type"] == "scalar":
                    value = float(value)
                elif p["type"] == "vector":
                    if len(value)==2:
                        value=Gf.Vec4f(*value,*p.get("default",[0,0,0,1])[2:])
                    else:
                        value = Gf.Vec4f(*value) if len(value) == 4 else Gf.Vec3f(*value)
                else:
                    if not isinstance(value, str) or not value.startswith("/"):
                        raise ValueError(f"Texture overrides require a cooked UE asset path: {path}")
                fields[p["parameter"]] = value
            overrides[path] = fields
            bindings.append(dict(path=path, asset=entry["asset"], model=entry["model"], parameters=list(fields)))
            bound_entries[path]=entry
            owned.add(path)
        if asset_map != (view.GetRendererSetting("nativeMaterialAssets") or {}):
            view.SetRendererSetting("nativeMaterialAssets", asset_map)
        if overrides != (view.GetRendererSetting("nativeMaterialParameters") or {}):
            view.SetRendererSetting("nativeMaterialParameters", overrides)
        self.owned, self.bindings = owned, bindings
        self.bound_entries=bound_entries
        from surface_effects import resolve
        try:
            effects, self.surface_diagnostics = resolve(model.stage, bound_entries, model.currentFrame)
        except ValueError as error:
            view.SetRendererSetting("wetValidationError", str(error))
            raise
        view.SetRendererSetting("wetValidationError", "")
        if effects != (view.GetRendererSetting("surfaceEffects") or {}):
            view.SetRendererSetting("surfaceEffects", effects)
        # Keep only an identity token: retaining the Engine object postpones
        # its GL resource destruction until after the Qt context has closed.
        self.last_renderer, self.last_time, self.dirty = id(view._renderer), model.currentFrame, False
