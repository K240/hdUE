"""Caller-owned ctypes mirror of the optional wet surface extension."""
import ctypes as C
U32,U64,Result=C.c_uint32,C.c_uint64,C.c_int32


class Binding(C.Structure):
    _fields_=[('struct_size',U32),('flags',U32)]+[(n,C.c_char_p) for n in ('object_id','variant_asset','preview_asset')]+[
        ('geometry_revision',U64),('amount',C.c_float),('roughness',C.c_float),('mask_count',U32),('reserved',U32),('mask',C.POINTER(C.c_float))]
class Batch(C.Structure):
    _fields_=[('struct_size',U32),('binding_count',U32),('bindings',C.POINTER(Binding))]
class Status(C.Structure):
    _fields_=[(n,U32) for n in ('struct_size','binding_count','upload_pending','reserved')]+[
        (n,U64) for n in ('mask_uploads','mask_bytes','geometry_uploads','updates')]
class Api(C.Structure):
    _fields_=[('struct_size',U32),('abi_version',U32),('apply',C.c_void_p),('get_status',C.c_void_p)]


class SurfaceApi:
    def __init__(self,runtime):
        self.runtime=runtime
        if tuple(map(C.sizeof,(Binding,Batch,Status,Api)))!=(64,16,48,24): raise RuntimeError('Surface ABI layout mismatch')
        self.get=runtime.library.hdue_get_surface_api
        self.get.argtypes=[U32,U32,C.POINTER(Api)]; self.get.restype=Result
        table=Api(); runtime.check(self.get(1,C.sizeof(table),C.byref(table)),'surface API')
        if table.struct_size!=24 or table.abi_version!=1 or not table.apply or not table.get_status: raise RuntimeError('Incomplete surface API')
        self.apply=C.CFUNCTYPE(Result,U64,C.POINTER(Batch))(table.apply)
        self.get_status=C.CFUNCTYPE(Result,U64,C.POINTER(Status))(table.get_status)

    def status(self,session):
        result=Status(struct_size=C.sizeof(Status))
        self.runtime.check(self.get_status(session,C.byref(result)),'surface status')
        return {name:int(getattr(result,name)) for name,_ in Status._fields_ if name not in ('struct_size','reserved')}
