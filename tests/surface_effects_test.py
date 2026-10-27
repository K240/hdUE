"""USD composition, mask-domain and prepared-capability contracts."""
import sys
from pathlib import Path
import unittest
from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from surface_effects import resolve, settings, validate_mask


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.stage=Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(self.stage,'/World')
        self.mesh=UsdGeom.Mesh.Define(self.stage,'/World/Mesh')
        self.mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
        self.mesh.CreateFaceVertexCountsAttr([3,3]); self.mesh.CreateFaceVertexIndicesAttr([0,1,2,0,2,3])
        material=UsdShade.Material.Define(self.stage,'/Looks/Base')
        UsdShade.MaterialBindingAPI.Apply(self.mesh.GetPrim()).Bind(material)
        self.entries={'/Looks/Base':dict(surface_effects=dict(wet=dict(version=1,supported=True,
            asset='/Game/Base_Wet.Base_Wet',preview_asset='/Game/Preview.Preview')))}
        self.time=Usd.TimeCode.Default()

    def author(self,path,name,kind,value):
        self.stage.GetPrimAtPath(path).CreateAttribute('hdue:surface:wet:'+name,kind).Set(value)

    def test_nearest_independent_opinions_and_subset(self):
        self.author('/World','enabled',Sdf.ValueTypeNames.Bool,True)
        self.author('/World','amount',Sdf.ValueTypeNames.Float,.7)
        self.author('/World/Mesh','roughness',Sdf.ValueTypeNames.Float,.2)
        subset=UsdGeom.Subset.CreateGeomSubset(UsdGeom.Imageable(self.mesh),'Faces','face',[1],'materialBind','nonOverlapping')
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(UsdShade.Material(self.stage.GetPrimAtPath('/Looks/Base')))
        self.author(str(subset.GetPath()),'amount',Sdf.ValueTypeNames.Float,.3)
        result,_=resolve(self.stage,self.entries,self.time)
        self.assertAlmostEqual(result['/World/Mesh']['amount'],.7)
        self.assertAlmostEqual(result['/World/Mesh/Faces']['amount'],.3)
        self.author(str(subset.GetPath()),'enabled',Sdf.ValueTypeNames.Bool,False)
        self.assertNotIn('/World/Mesh/Faces',resolve(self.stage,self.entries,self.time)[0])

    def test_all_mask_domains_and_indices(self):
        for domain,count in [('constant',1),('uniform',2),('vertex',4),('varying',4),('faceVarying',6)]:
            pv=UsdGeom.PrimvarsAPI(self.mesh).CreatePrimvar('mask',Sdf.ValueTypeNames.FloatArray,domain)
            pv.Set(Vt.FloatArray([0,1])); pv.SetIndices(Vt.IntArray([i%2 for i in range(count)]))
            self.assertEqual(validate_mask(self.mesh,'mask',self.time)['count'],count)
            pv.SetIndices(Vt.IntArray([2]*count))
            with self.assertRaises(ValueError): validate_mask(self.mesh,'mask',self.time)
        self.stage.GetPrimAtPath('/World/Mesh').RemoveProperty('primvars:mask:indices')
        pv.Set(Vt.FloatArray([float('nan')]*6))
        with self.assertRaises(ValueError): validate_mask(self.mesh,'mask',self.time)

    def test_disabled_zero_and_missing_profile(self):
        self.assertEqual(resolve(self.stage,{},self.time)[0],{})
        self.author('/World','enabled',Sdf.ValueTypeNames.Bool,True)
        with self.assertRaisesRegex(ValueError,'WetProfile'): resolve(self.stage,{},self.time)
        self.author('/World','amount',Sdf.ValueTypeNames.Float,0)
        self.assertEqual(resolve(self.stage,{},self.time)[0],{})

    def test_explicit_empty_and_time_samples(self):
        self.author('/World','maskPrimvar',Sdf.ValueTypeNames.Token,'parent_mask')
        self.author('/World/Mesh','maskPrimvar',Sdf.ValueTypeNames.Token,'')
        attr=self.mesh.GetPrim().CreateAttribute('hdue:surface:wet:amount',Sdf.ValueTypeNames.Float)
        attr.Set(0,1); attr.Set(1,3)
        values,sources=settings(self.mesh.GetPrim(),Usd.TimeCode(2))
        self.assertEqual(values['maskPrimvar'],''); self.assertAlmostEqual(values['amount'],.5)
        self.assertEqual(sources['maskPrimvar'],'/World/Mesh')


if __name__=='__main__': unittest.main()
