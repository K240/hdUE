"""Physical scene placement and native asset dimensions across source spaces."""
import sys
import unittest
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scene_space import normalize_stage,ue_basis
from asset_proxy import author_proxy

class SpaceTest(unittest.TestCase):
    def test_z_centimeters_hierarchy_reset_animation_and_camera(self):
        stage=Usd.Stage.CreateInMemory()
        parent=UsdGeom.Xform.Define(stage,'/World')
        parent.AddTranslateOp().Set(Gf.Vec3d(100,200,300))
        rotation=parent.AddRotateZOp(); rotation.Set(30.); rotation.Set(30.,0); rotation.Set(90.,10)
        child=UsdGeom.Xform.Define(stage,'/World/Child')
        op=child.AddTranslateOp(); op.Set(Gf.Vec3d(10,20,30),0); op.Set(Gf.Vec3d(20,40,60),10)
        reset=UsdGeom.Xform.Define(stage,'/World/Reset'); reset.SetResetXformStack(True)
        reset.AddTranslateOp().Set(Gf.Vec3d(30,40,50))
        camera=UsdGeom.Camera.Define(stage,'/Camera'); camera.AddTranslateOp().Set(Gf.Vec3d(100,200,300))
        camera.CreateFocalLengthAttr(50); camera.CreateClippingRangeAttr(Gf.Vec2f(1,10000))
        conversion=ue_basis('Z',.01)*ue_basis('Y',1).GetInverse()
        before={str(p.GetPath()):[(t,UsdGeom.Xformable(p).ComputeLocalToWorldTransform(t)) for t in (0,5,10)] for p in (child.GetPrim(),reset.GetPrim())}
        normalize_stage(stage,'Z',.01)
        for path,values in before.items():
            for time,matrix in values:
                self.assertTrue(Gf.IsClose(UsdGeom.Xformable(stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(time),matrix*conversion,1e-9))
        self.assertTrue(UsdGeom.Xformable(stage.GetPrimAtPath('/World/Reset')).GetResetXformStack())
        self.assertAlmostEqual(camera.GetFocalLengthAttr().Get(),.5)
        self.assertTrue(Gf.IsClose(camera.GetClippingRangeAttr().Get(),Gf.Vec2f(.01,100),1e-6))
        matrix=camera.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        self.assertTrue(Gf.IsClose(matrix.ExtractTranslation(),conversion.Transform(Gf.Vec3d(100,200,300)),1e-9))
        self.assertAlmostEqual(matrix.TransformDir(Gf.Vec3d(1,0,0)).GetLength(),1.)

    def test_native_asset_keeps_centimeters_and_parent_transform(self):
        for axis,meters in (('Z',.01),('Y',1.),('Y',.01)):
            with self.subTest(axis=axis,meters=meters):
                stage=Usd.Stage.CreateInMemory()
                parent=UsdGeom.Xform.Define(stage,'/World')
                parent.AddTranslateOp().Set(Gf.Vec3d(2,3,4))
                parent.AddRotateYOp().Set(25.)
                parent.AddScaleOp().Set(Gf.Vec3f(-2,3,4))
                prim=UsdGeom.Xform.Define(stage,'/World/Asset').GetPrim()
                UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(1,2,3))
                prim.CreateAttribute('hdue:asset',Sdf.ValueTypeNames.String).Set('/Game/A.A')
                old=UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                before=ue_basis(axis,meters).GetInverse()*old*ue_basis(axis,meters)
                normalize_stage(stage,axis,meters)
                proxy=author_proxy(stage,str(prim.GetPath()),'/Game/A.A',{},100.,'Y')
                correction=prim.GetAttribute('hdue:assetSpace')
                UsdGeom.Xformable(proxy).AddTransformOp().Set(correction.Get() if correction else Gf.Matrix4d(1))
                world=proxy.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                actual=ue_basis('Y',1).GetInverse()*world*ue_basis('Y',1)
                self.assertTrue(Gf.IsClose(before,actual,1e-8))
                # Repeated preparation reuses only proxies marked as generated.
                self.assertEqual(author_proxy(stage,str(prim.GetPath()),'/Game/B.B',{},100.,'Y').GetPath(),proxy.GetPath())

if __name__=='__main__': unittest.main()
