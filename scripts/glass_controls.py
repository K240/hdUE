"""Opt-in glass profiles for meshes using the displayColor fallback material."""
import hashlib
import json
import math
from pathlib import Path
from pxr import Gf,UsdGeom,UsdShade

GLASS_ASSET='/Game/HdUE/M_Glass.M_Glass'
DEFAULTS={'IOR':1.5,'Roughness':.04,'AttenuationColor':[.95,.98,.95],
          'AttenuationDistanceCm':1.,'SurfaceThicknessCm':.2,'Coverage':1.}

def validate_parameters(fields):
    if not isinstance(fields,dict) or set(fields)-set(DEFAULTS): raise ValueError('Unknown glass parameter')
    result={**DEFAULTS,**fields}
    for key in DEFAULTS:
        values=result[key] if key=='AttenuationColor' else [result[key]]
        if key=='AttenuationColor' and (not isinstance(values,(list,tuple)) or len(values)!=3): raise ValueError('AttenuationColor requires three linear RGB values')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values): raise ValueError(f'Glass {key} must be finite')
        if key=='IOR' and not 1<=values[0]<=2.5: raise ValueError('Glass IOR must be in [1, 2.5]')
        if key in ('Roughness','Coverage') and not 0<=values[0]<=1: raise ValueError(f'{key} must be in [0, 1]')
        if key=='AttenuationColor' and any(not 0<v<=1 for v in values): raise ValueError('AttenuationColor must be in (0, 1]')
        if key in ('AttenuationDistanceCm','SurfaceThicknessCm') and not 0<values[0]<=100000: raise ValueError(f'{key} must be positive centimeters')
        result[key]=list(map(float,values)) if key=='AttenuationColor' else float(values[0])
    return result

class GlassProfile:
    def __init__(self,file,controller):
        self.file=Path(file).resolve(); self.controller=controller
        self.entries={}; self.owned=set(); self.dirty=True; self.last_renderer=None; self.preparation_error=None; self.reload_error=False
        self.reload()
        if self.preparation_error: raise ValueError(self.preparation_error)
        controller._dataModel.signalStageReplaced.connect(self.invalidate)
        controller._dataModel.signalPrimsChanged.connect(self.invalidate)

    def reload(self):
        try:
            data=json.loads(self.file.read_text(encoding='utf-8-sig'))
            if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('materials'),dict): raise ValueError('Invalid glass profile version/materials')
            source=Path(data['stage']).resolve()
            if hashlib.sha256(source.read_bytes()).hexdigest()!=data['source_sha256']: raise ValueError('Glass profile USD source changed; regenerate the profile')
            entries={path:validate_parameters(fields) for path,fields in data['materials'].items()}
            if not entries: raise ValueError('Glass profile has no targets')
            self.source=source; self.entries=entries; self.preparation_error=None; self.reload_error=False; self.dirty=True
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.preparation_error=str(error)
            self.reload_error=True

    def invalidate(self,*args): self.dirty=True

    def sync(self):
        if self.reload_error: return False
        try:
            model=self.controller._dataModel; view=self.controller._stageView
            if not model.stage or view._renderer is None: return True
            if not self.dirty and self.last_renderer==id(view._renderer): return True
            if Path(model.stage.GetRootLayer().realPath).resolve()!=self.source: raise ValueError('Glass profile belongs to another USD stage')
            # Validate the whole profile before changing renderer settings.
            entries={}
            for path,fields in self.entries.items():
                prim=model.stage.GetPrimAtPath(path)
                if not prim or not prim.IsA(UsdGeom.Mesh): raise ValueError(f'Glass target mesh is absent: {path}')
                if UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]: raise ValueError(f'Glass profiles require an unbound displayColor mesh: {path}')
                entries[path+'.displayColor']=validate_parameters(fields)
            assets=dict(view.GetRendererSetting('nativeMaterialAssets') or {})
            parameters=dict(view.GetRendererSetting('nativeMaterialParameters') or {})
            for path in self.owned: assets.pop(path,None); parameters.pop(path,None)
            for path,fields in entries.items():
                assets[path]=GLASS_ASSET
                parameters[path]={key:Gf.Vec3f(*value) if key=='AttenuationColor' else value for key,value in fields.items()}
            if assets!=(view.GetRendererSetting('nativeMaterialAssets') or {}): view.SetRendererSetting('nativeMaterialAssets',assets)
            if parameters!=(view.GetRendererSetting('nativeMaterialParameters') or {}): view.SetRendererSetting('nativeMaterialParameters',parameters)
            self.owned=set(entries); self.dirty=False; self.last_renderer=id(view._renderer); self.preparation_error=None
            return True
        except (ValueError,TypeError) as error:
            self.preparation_error=str(error)
            return False
