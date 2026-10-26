"""Optional, versioned VT residency C ABI. All calls belong to the UE owner thread."""
import ctypes as C

U32,U64=C.c_uint32,C.c_uint64
Result=C.c_int32
class Options(C.Structure):
    _fields_=[("struct_size",U32),("maximum_pages",U32),("timeout_seconds",C.c_double)]
class Status(C.Structure):
    _fields_=[("struct_size",U32),("phase",U32)]+[(n,U64) for n in (
        "generation","required_pages","resident_pages","locked_tiles","required_mappings","ready_mappings")]+[
        ("textures",U32),("preparation_frames",U32),("preparation_ms",C.c_double),("message",C.c_char*512)]
class Api(C.Structure):
    _fields_=[("struct_size",U32),("abi_version",U32),("configure",C.c_void_p),("get_status",C.c_void_p)]
class ResidencyApi:
    def __init__(self,library):
        get=library.hdue_get_residency_api
        get.argtypes=[U32,U32,C.POINTER(Api)]; get.restype=Result
        self.api=Api()
        if get(1,C.sizeof(Api),C.byref(self.api)) or self.api.struct_size!=C.sizeof(Api) or self.api.abi_version!=1:
            raise RuntimeError("Incompatible VT residency ABI")
        self.configure_fn=C.CFUNCTYPE(Result,U64,C.POINTER(Options))(self.api.configure)
        self.status_fn=C.CFUNCTYPE(Result,U64,C.POINTER(Status))(self.api.get_status)
    def configure(self,session,maximum_pages=0,timeout_seconds=120):
        value=Options(C.sizeof(Options),maximum_pages,timeout_seconds)
        code=self.configure_fn(session,C.byref(value))
        if code: raise RuntimeError(f"Configure residency failed ({code})")
    def status(self,session):
        value=Status(); value.struct_size=C.sizeof(value)
        code=self.status_fn(session,C.byref(value))
        if code: raise RuntimeError(f"Get residency failed ({code})")
        return {name:(getattr(value,name).decode("utf-8") if name=="message" else getattr(value,name)) for name,_ in value._fields_}
