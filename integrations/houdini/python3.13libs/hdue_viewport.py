"""Caller-owned C diagnostics and readback for the Houdini delegate."""
import ctypes as C
from pathlib import Path
from hdue_runtime import Api,U32,U64,Result
from material_api import Parameter

class Frame(C.Structure):
    _fields_=[(n,U32) for n in ('struct_size','width','height','pixel_format','color_space','row_stride_bytes')]+[
        (n,U64) for n in ('generation','frame_id','required_bytes')]+[
        ('completed_samples',U32),('converged',U32),('request_to_frame_ms',C.c_double),('readback_ms',C.c_double)]
class Status(C.Structure):
    _fields_=[(n,U32) for n in ('struct_size','active','create_thread','execute_thread')]+[
        (n,U64) for n in ('session','generation','published_frames','scene_submissions')]+[
        (n,U32) for n in ('mode','meshes','materials','lights')]+[('frame',Frame),('error',C.c_char*512)]
class Options(C.Structure):
    _fields_=[(n,U32) for n in ('struct_size','mode','samples','lumen','auto_light','z_up')]+[
        ('meters_per_unit',C.c_double),('time_seconds',C.c_double)]
class DepthApi(C.Structure):
    _fields_=[('struct_size',U32),('abi_version',U32),('set_enabled',C.c_void_p),('copy_depth',C.c_void_p)]
class Material(C.Structure):
    _fields_=[('material_id',C.c_char_p),('native_asset',C.c_char_p),('parameter_count',U32),('reserved',U32),('parameters',C.POINTER(Parameter))]
class Materials(C.Structure):
    _fields_=[('struct_size',U32),('abi_version',U32),('session',U64),('material_count',U32),('reserved',U32),('materials',C.POINTER(Material))]

class Viewport:
    def __init__(self,runtime,plugin):
        if C.sizeof(Frame)!=72 or C.sizeof(Status)!=648 or C.sizeof(Options)!=40: raise RuntimeError('Invalid viewport ABI layout')
        self.runtime=runtime
        self.library=C.CDLL(str(Path(plugin).resolve()),winmode=0x1100)
        self.get=self.library.hdue_get_viewport_status; self.get.argtypes=[C.POINTER(Status)]; self.get.restype=Result
        self.set=self.library.hdue_set_viewport_options; self.set.argtypes=[C.POINTER(Options)]; self.set.restype=Result
        self.set_materials=self.library.hdue_set_viewport_materials
        self.set_materials.argtypes=[C.POINTER(Materials)]; self.set_materials.restype=Result
        if C.sizeof(Material)!=32 or C.sizeof(Materials)!=32: raise RuntimeError('Invalid viewport material ABI')
        api=Api(); runtime.check(runtime.library.hdue_get_api(1,C.sizeof(api),C.byref(api)),'readback API')
        self.poll=C.CFUNCTYPE(Result,U64,C.POINTER(Frame))(api.poll_frame)
        self.copy=C.CFUNCTYPE(Result,U64,U64,C.c_void_p,U64)(api.copy_frame)
        self.get_depth=runtime.library.hdue_get_depth_api
        self.get_depth.argtypes=[U32,U32,C.POINTER(DepthApi)]; self.get_depth.restype=Result
        extension=DepthApi()
        runtime.check(self.get_depth(1,C.sizeof(extension),C.byref(extension)),'depth extension')
        if extension.struct_size!=24 or extension.abi_version!=1: raise RuntimeError('Invalid depth extension layout')
        self.set_depth=C.CFUNCTYPE(Result,U64,U32)(extension.set_enabled)
        self.copy_depth=C.CFUNCTYPE(Result,U64,U64,C.c_void_p,U64)(extension.copy_depth)

    def status(self):
        s=Status(); s.struct_size=C.sizeof(s)
        self.runtime.check(self.get(C.byref(s)),'viewport status')
        result={n:getattr(s,n) for n,_ in s._fields_ if n not in ('frame','error')}
        result['error']=s.error.decode('utf-8','replace')
        result['frame']={n:getattr(s.frame,n) for n,_ in Frame._fields_}
        return result

    def options(self,mode,samples,meters_per_unit,z_up,time_seconds):
        options=Options(C.sizeof(Options),mode,samples,1,1,z_up,meters_per_unit,time_seconds)
        code=self.set(C.byref(options))
        if code==1: return False
        self.runtime.check(code,'viewport options')
        return True

    def pixels(self,status,require_converged=True):
        if not status['frame']['frame_id']: return None
        f=Frame(); f.struct_size=C.sizeof(f)
        code=self.poll(status['session'],C.byref(f))
        if code==1: return None
        self.runtime.check(code,'poll image')
        if f.generation!=status['generation'] or (require_converged and not f.converged): return None
        pixels=(C.c_float*(f.required_bytes//4))()
        code=self.copy(status['session'],f.frame_id,pixels,f.required_bytes)
        if code==HDUE_STALE_FRAME: return None
        self.runtime.check(code,'copy image')
        return f,pixels

    def materials(self,session,assets,overrides):
        storage=[]
        bindings=(Material*len(assets))()
        for index,(path,asset) in enumerate(sorted(assets.items())):
            fields=overrides.get(path,{})
            parameters=(Parameter*len(fields))()
            storage.append(parameters)
            for p,(name,value) in enumerate(sorted(fields.items())):
                parameters[p].name=name.encode('utf-8')
                if isinstance(value,str):
                    parameters[p].type=2; parameters[p].texture_asset=value.encode('utf-8')
                elif isinstance(value,(int,float)):
                    parameters[p].type=0; parameters[p].value[0]=float(value)
                else:
                    parameters[p].type=1
                    vector=list(value)
                    vector.extend([0.]*(4-len(vector)))
                    for component in range(4): parameters[p].value[component]=vector[component]
            bindings[index]=Material(path.encode('utf-8'),asset.encode('utf-8'),len(fields),0,parameters)
        update=Materials(C.sizeof(Materials),1,session,len(assets),0,bindings)
        code=self.set_materials(C.byref(update))
        if code==1: return False
        self.runtime.check(code,'viewport materials')
        return True

    def depth_pixels(self,status):
        frame=status['frame']
        if not frame['frame_id']: return None
        pixels=(C.c_float*(frame['width']*frame['height']))()
        code=self.copy_depth(status['session'],frame['frame_id'],pixels,C.sizeof(pixels))
        if code in (1,HDUE_STALE_FRAME): return None
        self.runtime.check(code,'copy depth image')
        return pixels

HDUE_STALE_FRAME=-7
