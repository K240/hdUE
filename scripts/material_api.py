"""ctypes mirror of the optional, caller-owned hdUE material C ABI."""
import ctypes as C

U32, U64, Result = C.c_uint32, C.c_uint64, C.c_int32


class RendererInfo(C.Structure):
    _fields_ = [(n,U32) for n in ("struct_size","substrate_enabled","adaptive_gbuffer","project_closures_per_pixel","bytes_per_pixel")]


class Parameter(C.Structure):
    _fields_ = [("name",C.c_char_p),("type",U32),("reserved",U32),("value",C.c_float*4),("texture_asset",C.c_char_p)]


class Update(C.Structure):
    _fields_ = [("struct_size",U32),("parameter_count",U32),("material_id",C.c_char_p),("parameters",C.POINTER(Parameter))]


class MaterialStats(C.Structure):
    _fields_ = [(n,U32) for n in ("struct_size","substrate_enabled","closure_count","bytes_per_pixel",
                                 "scalar_parameters","vector_parameters","texture_parameters","blend_mode")]


class Api(C.Structure):
    _fields_ = [("struct_size",U32),("abi_version",U32)]+[(n,C.c_void_p) for n in ("get_renderer_info","set_parameters","get_material_stats")]


class MaterialApi:
    def __init__(self, runtime):
        self.runtime = runtime
        if tuple(map(C.sizeof,(RendererInfo,Parameter,Update,MaterialStats,Api))) != (20,40,24,32,32):
            raise RuntimeError("Material extension C ABI layout mismatch")
        self.get_api = runtime.library.hdue_get_material_api
        self.get_api.argtypes = [U32,U32,C.POINTER(Api)]
        self.get_api.restype = Result
        table = Api()
        self.require(self.get_api(1,C.sizeof(table),C.byref(table)))
        if table.struct_size != 32 or table.abi_version != 1 or any(not getattr(table,n) for n,_ in Api._fields_[2:]):
            raise RuntimeError("Incompatible material extension")
        self.get_info = C.CFUNCTYPE(Result,C.POINTER(RendererInfo))(table.get_renderer_info)
        self.set_parameters = C.CFUNCTYPE(Result,U64,C.POINTER(Update))(table.set_parameters)
        self.get_stats = C.CFUNCTYPE(Result,U64,C.c_char_p,C.POINTER(MaterialStats))(table.get_material_stats)

    def require(self, result):
        if result != 0:
            raise RuntimeError(f"Material API {result}: {self.runtime.status().message.decode('utf-8','replace')}")

    def renderer_info(self):
        info = RendererInfo(struct_size=C.sizeof(RendererInfo))
        self.require(self.get_info(C.byref(info)))
        return {n:int(getattr(info,n)) for n,_ in info._fields_ if n != "struct_size"}

    def material_stats(self, session, material):
        stats = MaterialStats(struct_size=C.sizeof(MaterialStats))
        self.require(self.get_stats(session,material.encode(),C.byref(stats)))
        return {n:int(getattr(stats,n)) for n,_ in stats._fields_ if n != "struct_size"}

    def edit(self, session, material, fields, check=True):
        parameters = []
        for name,value in fields:
            p = Parameter(name=name.encode())
            if isinstance(value,str):
                p.type=2; p.texture_asset=value.encode()
            elif isinstance(value,(int,float)):
                p.type=0; p.value[0]=value
            else:
                p.type=1
                for i,v in enumerate(value):
                    p.value[i]=v
                if len(value)==3:
                    p.value[3]=1
            parameters.append(p)
        array=(Parameter*len(parameters))(*parameters)
        update=Update(struct_size=C.sizeof(Update),parameter_count=len(parameters),material_id=material.encode(),parameters=array)
        result=self.set_parameters(session,C.byref(update))
        if check:
            self.require(result)
        return result
