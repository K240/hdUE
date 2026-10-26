"""USD-only native asset bounds used by both usdview and Solaris."""
import itertools
from pxr import Gf, Sdf, UsdGeom


def author_proxy(stage, path, asset, catalog, units, axis, is_mesh=False):
    """Author into the caller's edit target; never change the source layer."""
    mesh_path=path if is_mesh else path+'/__HdUEAssetProxy'
    if not is_mesh and stage.GetPrimAtPath(mesh_path):
        existing=stage.GetPrimAtPath(mesh_path).GetAttribute('hdue:generatedAssetProxy')
        if not existing or existing.Get()!=True:
            raise ValueError('Reserved hdUE proxy path already exists: '+mesh_path)
    mesh=UsdGeom.Mesh.Define(stage,mesh_path)
    bounds=catalog.get(asset,{}).get('bounds_cm',{'min':[-50]*3,'max':[50]*3})
    def from_ue(p):
        return Gf.Vec3f(*(v/units for v in ((p[0],-p[1],p[2]) if axis=='Z' else (p[1],p[2],-p[0]))))
    points=[from_ue(p) for p in itertools.product(*zip(bounds['min'],bounds['max']))]
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([4]*6)
    mesh.CreateFaceVertexIndicesAttr([0,2,3,1,4,5,7,6,0,1,5,4,2,6,7,3,0,4,6,2,1,3,7,5])
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateExtentAttr([Gf.Vec3f(*(min(p[i] for p in points) for i in range(3))),
                           Gf.Vec3f(*(max(p[i] for p in points) for i in range(3)))])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('hdue:asset',Sdf.ValueTypeNames.String,UsdGeom.Tokens.constant).Set(asset)
    mesh.GetPrim().CreateAttribute('hdue:generatedAssetProxy',Sdf.ValueTypeNames.Bool,custom=True).Set(True)
    return mesh
