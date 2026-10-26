"""Verify host proxy composition, units and editable references with real USD."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pxr import Gf, Sdf, Usd, UsdGeom
from asset_controls import AssetReferences

class Signal:
    def connect(self,callback): pass

class AssetProxyTest(unittest.TestCase):
    def setUp(self):
        self.stage=Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(self.stage,UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(self.stage,.01)
        self.prim=UsdGeom.Xform.Define(self.stage,'/Asset').GetPrim()
        self.prim.CreateAttribute('hdue:asset',Sdf.ValueTypeNames.String).Set('/Game/M.M')
        model=SimpleNamespace(stage=self.stage,currentFrame=Usd.TimeCode.Default(),signalStageReplaced=Signal(),signalPrimsChanged=Signal())
        self.refs=AssetReferences(SimpleNamespace(_dataModel=model))

    def test_session_layer_bounds_edit_and_removal(self):
        original=self.stage.GetRootLayer().ExportToString()
        self.refs.catalog={'/Game/M.M':{'bounds_cm':{'min':[1,2,3],'max':[10,20,30]}}}
        self.refs.sync()
        proxy=UsdGeom.Mesh(self.stage.GetPrimAtPath('/Asset/__HdUEAssetProxy'))
        self.assertEqual(str(proxy.GetExtentAttr().Get()[0]),'(1, -20, 3)')
        self.assertEqual(UsdGeom.PrimvarsAPI(proxy).GetPrimvar('hdue:asset').Get(),'/Game/M.M')
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),original)
        self.stage.SetEditTarget(self.stage.GetSessionLayer())
        self.prim.GetAttribute('hdue:asset').Set('/Game/N.N'); self.refs.invalidate(); self.refs.sync()
        proxy=UsdGeom.Mesh(self.stage.GetPrimAtPath('/Asset/__HdUEAssetProxy'))
        self.assertEqual(UsdGeom.PrimvarsAPI(proxy).GetPrimvar('hdue:asset').Get(),'/Game/N.N')
        self.prim.GetAttribute('hdue:asset').Set(''); self.refs.invalidate(); self.refs.sync()
        self.assertFalse(self.stage.GetPrimAtPath('/Asset/__HdUEAssetProxy'))

    def test_y_up_meters(self):
        UsdGeom.SetStageUpAxis(self.stage,UsdGeom.Tokens.y); UsdGeom.SetStageMetersPerUnit(self.stage,1)
        self.refs.catalog={'/Game/M.M':{'bounds_cm':{'min':[100,200,300],'max':[400,500,600]}}}
        self.refs.sync()
        proxy=UsdGeom.Mesh(self.stage.GetPrimAtPath('/Asset/__HdUEAssetProxy'))
        self.assertEqual(str(proxy.GetExtentAttr().Get()[0]),'(2, 3, -4)')

    def test_loaded_bounds_use_parent_inverse_without_source_edits(self):
        UsdGeom.Xformable(self.prim).AddTranslateOp().Set(Gf.Vec3d(100,0,0))
        root=self.stage.GetRootLayer().ExportToString()
        self.refs.sync()
        calls=[]
        def status(session,id):
            calls.append((session,id))
            return dict(state=1,bounds_min=[110,20,30],bounds_max=[120,40,50])
        self.refs.api=SimpleNamespace(status=status)
        stats={'sessionHandle':7,'nativeAssetInstances':1,'nativeAssetIds':{'/Asset':'/delegate/Asset/__HdUEAssetProxy'}}
        self.refs.refresh_bounds(None,stats); self.refs.refresh_bounds(None,stats)
        self.assertEqual(calls,[(7,'/delegate/Asset/__HdUEAssetProxy')])
        proxy=UsdGeom.Mesh(self.stage.GetPrimAtPath('/Asset/__HdUEAssetProxy'))
        self.assertEqual(str(proxy.GetExtentAttr().Get()[0]),'(10, -40, 30)')
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),root)

if __name__=='__main__': unittest.main()
