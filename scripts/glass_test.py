"""Test IOR and absorption on profile-selected review geometry."""
import copy
import hashlib
import time
from pathlib import Path
import numpy as np
from pxr import Gf,Usd,UsdGeom
from scene_check import SceneCheck
from material_api import MaterialApi

class GlassTest(SceneCheck):
    def __init__(self,*args):
        super().__init__(*args); self.deadline=time.monotonic()+900
        self.comparisons=[]; self.material_checks=[]

    def image(self,label,samples):
        if self.launcher.glass_profile.preparation_error: raise AssertionError(self.launcher.glass_profile.preparation_error)
        previous=self.frames[-1]['generation'] if self.frames else 0
        return (yield from self.wait_image(label,previous,samples=samples))

    def material(self,path):
        info=self.api.material_stats(self.stats()['sessionHandle'],path+'.displayColor')
        if info['blend_mode']!=7 or not info['substrate_enabled'] or info['scalar_parameters']<5 or info['vector_parameters']<1:
            raise AssertionError(f'Wrong glass material: {path}, {info}')
        self.material_checks.append(dict(path=path,**info))

    def run(self):
        model=self.controller._dataModel; stage=model.stage
        root,session=stage.GetRootLayer(),stage.GetSessionLayer()
        digest=hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()
        original=session.ExportToString(); edit_target=stage.GetEditTarget()
        profile=self.launcher.glass_profile; original_entries=copy.deepcopy(profile.entries)
        targets=list(profile.entries)
        if len(targets)!=5: raise AssertionError('Glass review requires a five-target fixture')
        meshes=[p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),[UsdGeom.Tokens.default_])
        bounds={path:cache.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedBox() for path in targets}
        self.api=MaterialApi(self.launcher.runtime)
        model.viewSettings.showHUD=False; model.viewSettings.showBBoxes=False
        self.controller._mainWindow.resize(1600,1000); stage.SetEditTarget(session)
        try:
            for mode,samples in (('realtime',0),('pathtraced',64)):
                model.viewSettings.cameraPrim=None; session.ImportFromString(original)
                profile.entries=copy.deepcopy(original_entries); profile.invalidate()
                self.view.setFixedSize(1280,720)
                if mode=='pathtraced': self.launcher.select_mode(mode)
                yield
                yield from self.image(mode+'-scene-glass',samples)
                for path in targets: self.material(path)
                # Reject invalid optical inputs before changing any GPU values,
                # then resume from the last valid profile after correction.
                before=self.stats(); profile.entries[targets[0]]['IOR']=.5; profile.invalidate()
                for _ in range(5): yield
                blocked=self.stats()
                if not blocked['materialGraphBlocked'] or blocked['frameId']!=before['frameId'] or blocked['materialParameterUpdates']!=before['materialParameterUpdates']:
                    raise AssertionError('Invalid glass parameters reached the backend')
                profile.entries=copy.deepcopy(original_entries); profile.invalidate()
                yield
                yield from self.image(mode+'-profile-recovered',samples)
                for prim in meshes: UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                self.view.setFixedSize(800,600)
                camera=UsdGeom.Camera.Define(stage,'/HdUEGlassReview/Camera')
                camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(27)
                camera.CreateFocalLengthAttr(38); camera.CreateClippingRangeAttr(Gf.Vec2f(.01,100000))
                camera_op=camera.AddTransformOp(); model.viewSettings.cameraPrim=camera.GetPrim()
                panels=[]
                for tile in range(32):
                    mesh=UsdGeom.Mesh.Define(stage,f'/HdUEGlassReview/Stripe{tile}')
                    mesh.CreateFaceVertexCountsAttr([4]); mesh.CreateFaceVertexIndicesAttr([0,1,2,3]); mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
                    mesh.CreateDisplayColorAttr([Gf.Vec3f(.55,.22,.045) if tile%2 else Gf.Vec3f(.035,.16,.6)])
                    panels.append(mesh)
                previous_target=None
                for index,path in enumerate(targets):
                    prim=stage.GetPrimAtPath(path)
                    if previous_target: UsdGeom.Imageable(previous_target).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                    UsdGeom.Imageable(prim).GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                    box=bounds[path]; center=box.GetMidpoint(); radius=max(max(box.GetSize()),.1)*.75
                    eye=center+Gf.Vec3d(radius*.3,-radius*3.5,radius*.12)
                    camera_op.Set(Gf.Matrix4d().SetLookAt(eye,center,Gf.Vec3d(0,0,1)).GetInverse())
                    y=box.GetMax()[1]+radius*.4
                    for tile,panel in enumerate(panels):
                        x0=center[0]-radius*3+tile*(radius*6/32); x1=x0+radius*6/32
                        panel.CreatePointsAttr([Gf.Vec3f(x0,y,center[2]-radius*3),Gf.Vec3f(x1,y,center[2]-radius*3),
                            Gf.Vec3f(x1,y,center[2]+radius*3),Gf.Vec3f(x0,y,center[2]+radius*3)])
                    base=copy.deepcopy(original_entries[path]); pictures={}
                    controls=(('glass',{}),('ior-one',{'IOR':1.}),('green',{'AttenuationColor':[.25,.9,.35]}),
                        ('blue',{'AttenuationColor':[.35,.45,.95]}),('strong-absorption',{'AttenuationColor':[.25,.9,.35],'AttenuationDistanceCm':.25}),('restored',{}))
                    submitted=None
                    for label,changes in controls:
                        profile.entries[path]={**base,**changes}; profile.invalidate()
                        pictures[label]=yield from self.image(f'{mode}-{index:02}-{label}',samples)
                        self.material(path)
                        if submitted is None: submitted=self.stats()['submittedMeshes']
                        elif self.stats()['submittedMeshes']!=submitted: raise AssertionError('Glass parameter edits resubmitted geometry')
                    # Restrict numeric comparisons to the object region; unchanged
                    # background stripes should not dilute the glass effect.
                    crop=np.s_[90:510,180:620]
                    def delta(a,b): return float(np.mean(np.abs(pictures[a][crop].astype(float)-pictures[b][crop].astype(float))))
                    values=dict(ior_difference=delta('glass','ior-one'),absorption_color_difference=delta('green','blue'),
                                absorption_distance_difference=delta('green','strong-absorption'),restore_difference=delta('glass','restored'))
                    if min(values[k] for k in ('ior_difference','absorption_color_difference','absorption_distance_difference'))<.25:
                        raise AssertionError(f'Glass control has no visible effect: {mode}, {path}, {values}')
                    self.comparisons.append(dict(mode=mode,path=path,**values)); previous_target=prim
                    profile.entries[path]=copy.deepcopy(base); profile.invalidate()
        finally:
            model.viewSettings.cameraPrim=None; session.ImportFromString(original); stage.SetEditTarget(edit_target)
            profile.entries=original_entries; profile.invalidate()
        if hashlib.sha256(Path(root.realPath).read_bytes()).hexdigest()!=digest: raise AssertionError('Source USD changed')
        self.launcher.test_result=dict(glass_verified=True,targets=targets,frames=self.frames,comparisons=self.comparisons,
            material_checks=self.material_checks,profile=str(profile.file),usd_unchanged=True,source_sha256=digest,
            same_process=True,module_handles=self.launcher.runtime.module_handles(),parameter_edit_mesh_resubmissions=0,invalid_profile_rejected=True)
