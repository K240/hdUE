"""Caller-owned ctypes binding for the optional native scene asset C ABI."""
import ctypes as C
from hdue_runtime import U32, U64, Result

class AssetDesc(C.Structure):
    _fields_=[("struct_size",U32),("visible",U32),("id",C.c_char_p),("asset_path",C.c_char_p),("transform",C.c_double*16)]
class AssetStatus(C.Structure):
    _fields_=[(n,U32) for n in ("struct_size","kind","state","actors","meshes","lights")]+[
        ("bounds_min",C.c_double*3),("bounds_max",C.c_double*3),("message",C.c_char*512)]
class AssetSceneStatus(C.Structure):
    _fields_=[(n,U32) for n in ("struct_size","instances","loading","failed","actors","meshes","lights","reserved")]+[("message",C.c_char*512)]
class AssetApi(C.Structure):
    _fields_=[("struct_size",U32),("abi_version",U32)]+[(n,C.c_void_p) for n in ("upsert","remove","get_instance_status","get_scene_status")]

class Assets:
    def __init__(self,runtime):
        if tuple(C.sizeof(t) for t in (AssetDesc,AssetStatus,AssetSceneStatus,AssetApi))!=(152,584,544,40):
            raise RuntimeError("Wrong native asset C ABI layout")
        self.runtime=runtime
        get=runtime.library.hdue_get_asset_api
        get.argtypes=[U32,U32,C.POINTER(AssetApi)]; get.restype=Result
        api=AssetApi(); runtime.check(get(1,C.sizeof(api),C.byref(api)),"get_asset_api")
        if api.struct_size!=40 or api.abi_version!=1: raise RuntimeError("Invalid asset extension")
        self.upsert=C.CFUNCTYPE(Result,U64,C.POINTER(AssetDesc))(api.upsert)
        self.remove=C.CFUNCTYPE(Result,U64,C.c_char_p)(api.remove)
        self.get_status=C.CFUNCTYPE(Result,U64,C.c_char_p,C.POINTER(AssetStatus))(api.get_instance_status)
        self.get_scene=C.CFUNCTYPE(Result,U64,C.POINTER(AssetSceneStatus))(api.get_scene_status)

    def status(self,session,id):
        status=AssetStatus(); status.struct_size=C.sizeof(status)
        self.runtime.check(self.get_status(session,id.encode(),C.byref(status)),"asset_status")
        return {n:(list(getattr(status,n)) if n.startswith("bounds_") else getattr(status,n).decode("utf-8") if n=="message" else getattr(status,n)) for n,_ in status._fields_}
