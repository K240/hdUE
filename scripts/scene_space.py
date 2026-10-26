"""Normalize USD composition into Solaris Y-up meters in the caller's layer."""
import math
from pxr import Gf,Sdf,Usd,UsdGeom

def ue_basis(axis,meters):
    if axis not in ('Y','Z') or not math.isfinite(meters) or meters<=0:
        raise ValueError('Input space requires Y/Z up and positive meters per unit')
    basis=Gf.Matrix4d(1)
    rows=((0,1,0),(0,0,1),(-1,0,0)) if axis=='Y' else ((1,0,0),(0,-1,0),(0,0,1))
    for r,row in enumerate(rows):
        for c,value in enumerate(row): basis[r,c]=value*meters*100
    return basis

def _matrix_op(xform,left,right,suffix):
    # Keep original operations and their interpolation; baking only authored
    # matrices would change animated rotation between samples.
    original=xform.GetOrderedXformOps()
    reset=xform.GetResetXformStack()
    before=[]; after=[]
    if not Gf.IsClose(right,Gf.Matrix4d(1),1e-12):
        op=xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,suffix+'Post')
        op.Set(right); before.append(op)
    if not Gf.IsClose(left,Gf.Matrix4d(1),1e-12):
        op=xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,suffix+'Pre')
        op.Set(left); after.append(op)
    xform.SetXformOpOrder(before+original+after,resetXformStack=reset)

def normalize_stage(stage,axis,meters):
    """Preserve prim paths and source layers, including reset stacks and time samples.

    Geometry gets the source-to-Solaris transform once per independent xform
    hierarchy. Native assets retain a local-space correction because their
    intrinsic geometry already uses UE units rather than USD vertex units.
    """
    axis=str(axis).upper()
    conversion=ue_basis(axis,meters)*ue_basis('Y',1).GetInverse()
    if Gf.IsClose(conversion,Gf.Matrix4d(1),1e-12): return
    prims=list(stage.Traverse())
    def has_xform_parent(prim):
        parent=prim.GetParent()
        while parent and not parent.IsPseudoRoot():
            if UsdGeom.Xformable(parent): return True
            parent=parent.GetParent()
        return False
    for prim in prims:
        xform=UsdGeom.Xformable(prim)
        if xform and (not has_xform_parent(prim) or xform.GetResetXformStack()):
            _matrix_op(xform,Gf.Matrix4d(1),conversion,'hdueSolarisSpace')
        asset=prim.GetAttribute('hdue:asset')
        if asset and asset.Get() and (xform or has_xform_parent(prim)):
            prim.CreateAttribute('hdue:assetSpace',Sdf.ValueTypeNames.Matrix4d,custom=True).Set(conversion.GetInverse())
    # Camera filmback axes are always Y-up/-Z, regardless of stage up axis.
    # Remove only the unit scale from their basis, and convert optical lengths.
    scale_inverse=Gf.Matrix4d().SetScale(Gf.Vec3d(1/meters))
    for prim in prims:
        if not prim.IsA(UsdGeom.Camera): continue
        _matrix_op(UsdGeom.Xformable(prim),scale_inverse,Gf.Matrix4d(1),'hdueSolarisCameraScale')
        for name in ('focalLength','horizontalAperture','verticalAperture','horizontalApertureOffset','verticalApertureOffset','focusDistance','clippingRange'):
            attr=prim.GetAttribute(name)
            times=[Usd.TimeCode.Default()]+[Usd.TimeCode(t) for t in attr.GetTimeSamples()]
            values=[(t,attr.Get(t)) for t in times]
            for time,value in values:
                if value is not None: attr.Set(value*meters,time)
