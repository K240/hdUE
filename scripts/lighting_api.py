"""Caller-owned ctypes mirror of the optional lighting diagnostics ABI."""
import ctypes as C

U32,U64,Result=C.c_uint32,C.c_uint64,C.c_int32

class Status(C.Structure):
    _fields_=[(n,U32) for n in ('struct_size','light_count','dome_count','environment_pending',
        'mega_lights_project','mega_lights_allowed','mega_lights_active','cube_resolution')]+[
        ('frame_id',U64),('generation',U64),('environment_max_radiance',C.c_float),('reserved',U32)]

class Api(C.Structure):
    _fields_=[('struct_size',U32),('abi_version',U32),('upsert',C.c_void_p),('get_status',C.c_void_p)]

class Lighting:
    def __init__(self,runtime):
        if C.sizeof(Status)!=56 or C.sizeof(Api)!=24: raise RuntimeError('Invalid lighting ABI layout')
        self.runtime=runtime
        get=runtime.library.hdue_get_lighting_api
        get.argtypes=[U32,U32,C.POINTER(Api)]; get.restype=Result
        api=Api()
        runtime.check(get(1,C.sizeof(api),C.byref(api)),'lighting API')
        if api.struct_size!=C.sizeof(api) or api.abi_version!=1: raise RuntimeError('Unsupported lighting ABI')
        self.get_status=C.CFUNCTYPE(Result,U64,C.POINTER(Status))(api.get_status)

    def status(self,session):
        status=Status(); status.struct_size=C.sizeof(status)
        self.runtime.check(self.get_status(session,C.byref(status)),'lighting status')
        return {n:getattr(status,n) for n,_ in status._fields_}
