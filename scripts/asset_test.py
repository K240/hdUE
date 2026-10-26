"""Render the user's native Level, StaticMesh and Actor BP through Hydra."""
import hashlib
import time
from pathlib import Path
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom
from usdview_test import SmokeTest
from asset_runtime import Assets

class AssetTest(SmokeTest):
    def __init__(self,*args):
        super().__init__(*args); self.deadline=time.monotonic()+1200
        self.instances=[]

    def image(self,label,previous=0,samples=0,allow_recovery=False):
        self.view.updateGL(); start=time.monotonic()
        while True:
            stats=self.view._renderer.GetRenderStats() if allow_recovery else self.stats()
            if stats.get('error') and allow_recovery:
                yield; continue
            if stats.get('generation',0)>previous and stats.get('frameId') and not stats.get('nativeAssetLoading') and self.view.IsRendererConverged(): break
            yield
        if stats.get('completedSamples',0)<samples or stats.get('nativeAssetFailed'): raise AssertionError(stats)
        yield
        from pxr.Usdviewq.qt import QtGui
        image=self.view.grabFrameBuffer().convertToFormat(QtGui.QImage.Format_RGBA8888)
        rgb=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)[...,:3].copy()
        image.save(str(self.output/(label+'.png')))
        self.frames.append(dict(label=label,seconds=time.monotonic()-start,mean_rgb=float(rgb.mean()),lit_fraction=float((rgb.max(axis=2)>10).mean()),**stats))
        print('asset-check',self.frames[-1],flush=True)
        if rgb.std()<2: raise AssertionError(f'{label}: uniform/empty native asset image')
        return rgb

    def camera(self,path):
        self.controller._dataModel.viewSettings.cameraPrim=self.controller._dataModel.stage.GetPrimAtPath(path)
        self.view.updateGL()

    def inspect(self,path,kind):
        stats=self.stats()
        info=self.api.status(stats['sessionHandle'],stats['nativeAssetIds'][path])
        if info['kind']!=kind or info['state']!=1 or not info['actors'] or not info['meshes']: raise AssertionError(info)
        self.instances.append(dict(path=path,**info))
        return info

    def run(self):
        model=self.controller._dataModel; stage=model.stage
        source=Path(stage.GetRootLayer().realPath); digest=hashlib.sha256(source.read_bytes()).hexdigest()
        self.api=Assets(self.launcher.runtime)
        model.viewSettings.showHUD=False; model.viewSettings.showBBoxes=False
        self.view.setFixedSize(960,720); self.controller._mainWindow.resize(1400,1000)
        session=stage.GetSessionLayer(); old=session.ExportToString(); target=stage.GetEditTarget(); stage.SetEditTarget(session)
        level=UsdGeom.Imageable(stage.GetPrimAtPath('/World/Level'))
        mesh=UsdGeom.Imageable(stage.GetPrimAtPath('/World/Mesh'))
        bp=UsdGeom.Imageable(stage.GetPrimAtPath('/World/Blueprint'))
        try:
            for mode,samples in (('realtime',0),('pathtraced',64)):
                level.CreateVisibilityAttr().Set('inherited'); mesh.CreateVisibilityAttr().Set('invisible'); bp.CreateVisibilityAttr().Set('invisible')
                self.camera('/Cameras/Level')
                if mode=='pathtraced': self.launcher.select_mode(mode)
                previous=self.frames[-1]['generation'] if self.frames else 0
                yield from self.image(mode+'-level',previous,samples)
                self.inspect('/World/Level',4)
                if self.stats()['viewerLights'] or self.stats()['nativeLights']!=2: raise AssertionError('Level lights were replaced by viewer lights')
                if stage.GetPrimAtPath('/Cameras/LevelDetail'):
                    self.camera('/Cameras/LevelDetail')
                    yield from self.image(mode+'-level-detail',self.frames[-1]['generation'],samples)
                level.CreateVisibilityAttr().Set('invisible'); mesh.CreateVisibilityAttr().Set('inherited'); self.camera('/Cameras/Mesh')
                first=yield from self.image(mode+'-static-mesh',self.frames[-1]['generation'],samples)
                original=self.inspect('/World/Mesh',1)
                if self.stats()['viewerLights']!=2: raise AssertionError('Hidden Level lights suppressed mesh viewer lights')
                transform=UsdGeom.Xformable(mesh).AddTranslateOp(opSuffix='test') if mode=='realtime' else UsdGeom.Xformable(mesh).GetOrderedXformOps()[-1]
                transform.Set(Gf.Vec3d(80,0,0))
                moved=yield from self.image(mode+'-mesh-moved',self.frames[-1]['generation'],samples)
                moved_info=self.inspect('/World/Mesh',1)
                if abs(moved_info['bounds_min'][0]-original['bounds_min'][0]-80)>1: raise AssertionError('Native transform did not propagate')
                if np.abs(first.astype(float)-moved.astype(float)).mean()<.1: raise AssertionError('Transform produced no image change')
                transform.Set(Gf.Vec3d(0))
                mesh.CreateVisibilityAttr().Set('invisible'); bp.CreateVisibilityAttr().Set('inherited'); self.camera('/Cameras/Blueprint')
                yield from self.image(mode+'-blueprint',self.frames[-1]['generation'],samples)
                self.inspect('/World/Blueprint',3)
            # Invalid resources/missing paths become recoverable per-instance failures.
            prim=stage.GetPrimAtPath('/World/Mesh'); attr=prim.GetAttribute('hdue:asset'); asset=attr.Get()
            attr.Set('/Game/HdUE/M_PreviewSurface.M_PreviewSurface')
            for _ in range(100):
                self.view.updateGL(); stats=self.view._renderer.GetRenderStats()
                if stats.get('nativeAssetFailed'): break
                yield
            else: raise AssertionError('Unplaceable material was not rejected')
            if 'resource' not in stats['error']: raise AssertionError(stats)
            attr.Set('/Game/Absent.Absent')
            for _ in range(100):
                self.view.updateGL(); stats=self.view._renderer.GetRenderStats()
                if 'absent' in stats.get('error',''): break
                yield
            else: raise AssertionError('Missing cooked asset was not diagnosed')
            attr.Set(asset)
            yield from self.image('recovered',self.frames[-1]['generation'],64,allow_recovery=True)
            # Removing a Level must finish its asynchronous unload before PT.
            stage.OverridePrim('/World/Level').SetActive(False)
            yield from self.image('level-unloaded',self.frames[-1]['generation'],64)
            if self.stats()['nativeAssetInstances']!=2 or self.stats()['nativeLights']:
                raise AssertionError('Deleted Level/lighting remained in the scene')
            stage.OverridePrim('/World/Level').SetActive(True)
            level=UsdGeom.Imageable(stage.GetPrimAtPath('/World/Level'))
            level.CreateVisibilityAttr().Set('inherited'); bp.CreateVisibilityAttr().Set('invisible')
            self.camera('/Cameras/Level')
            yield from self.image('level-reloaded',self.frames[-1]['generation'],64)
            original_level=self.inspect('/World/Level',4)
            op=UsdGeom.Xformable(level).AddTranslateOp(opSuffix='nativeLevelTest'); op.Set(Gf.Vec3d(200,0,0))
            yield from self.image('level-moved',self.frames[-1]['generation'],64)
            moved_level=self.inspect('/World/Level',4)
            if abs(moved_level['bounds_min'][0]-original_level['bounds_min'][0]-200)>2:
                raise AssertionError('Level instance transform did not propagate')
            op.Set(Gf.Vec3d(0))
            # Delete/recreate the asset Prim, then destroy/recreate the delegate.
            stage.RemovePrim('/World/Blueprint')
            # The root Prim is stronger than removal in the session: deactivate it.
            stage.OverridePrim('/World/Blueprint').SetActive(False)
            level.CreateVisibilityAttr().Set('inherited'); self.camera('/Cameras/Level')
            yield from self.image('blueprint-removed',self.frames[-1]['generation'],64)
            stage.OverridePrim('/World/Blueprint').SetActive(True)
            yield from self.image('blueprint-recreated',self.frames[-1]['generation'],64)
            before=self.stats()['sessionHandle']; self.switch('HdStormRendererPlugin'); self.switch('HdUERendererPlugin'); self.launcher.configure_renderer()
            yield from self.image('delegate-recreated',0,64)
            if self.stats()['sessionHandle']==before or self.launcher.runtime.initializations!=1: raise AssertionError('Wrong runtime lifetime')
        finally:
            session.ImportFromString(old); stage.SetEditTarget(target)
        if hashlib.sha256(source.read_bytes()).hexdigest()!=digest: raise AssertionError('USD source modified')
        self.launcher.test_result=dict(frames=self.frames,instances=self.instances,source_unchanged=True,source_sha256=digest,
            invalid_assets_rejected=True,source_level_complete=not bool(self.launcher.asset_references.warnings),
            source_level_warnings=list(self.launcher.asset_references.warnings))
