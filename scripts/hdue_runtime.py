"""Main-thread UELibrary owner for Python hosts. ABI: include/hdue/bridge.h."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import threading

U32, U64, I64 = C.c_uint32, C.c_uint64, C.c_int64
Result = C.c_int32


class InitDesc(C.Structure):
    _fields_ = [("struct_size", U32), ("reserved", U32), ("instance", U64),
                ("window", U64), ("project", C.c_char_p), ("map", C.c_char_p),
                ("arguments", C.c_char_p)]


class Status(C.Structure):
    _fields_ = [(name, U32) for name in (
        "struct_size", "state", "world_ready", "rhi_ready", "engine_major",
        "engine_minor", "engine_patch", "render_mode_mask")] + [
        ("video_memory", U64), ("message", C.c_char * 512)]


class Api(C.Structure):
    _fields_ = [("struct_size", U32), ("abi_version", U32)] + [
        (name, C.c_void_p) for name in (
            "initialize", "get_status", "tick", "create_session", "destroy_session",
            "request_render", "poll_frame", "copy_frame", "window_message",
            "shutdown", "apply_scene")]


# Keep DLLs and the callback alive until process exit, including after shutdown.
_process_libraries = []


class Runtime:
    def __init__(self, library, project, log=None):
        if (C.sizeof(InitDesc), C.sizeof(Status), C.sizeof(Api)) != (48, 552, 96):
            raise RuntimeError("hdUE requires the Windows x64 ABI")
        self.owner = threading.get_ident()
        self.ready = False
        self.attempted = False
        self.closed = False
        self.ticks = 0
        self.initializations = 0
        self.backend_name=Path(library).name
        self.library = C.CDLL(str(Path(library).resolve()), winmode=0x1100)
        _process_libraries.append(self.library)
        get_api = self.library.hdue_get_api
        get_api.argtypes = [U32, U32, C.POINTER(Api)]
        get_api.restype = Result
        api = Api()
        if get_api(1, C.sizeof(api), C.byref(api)) or api.abi_version != 1 or api.struct_size != 96:
            raise RuntimeError("Incompatible hdUE C ABI")
        if any(not getattr(api, name) for name, _ in Api._fields_[2:]):
            raise RuntimeError("Incomplete hdUE C ABI")
        self.initialize = C.CFUNCTYPE(Result, C.POINTER(InitDesc))(api.initialize)
        self.get_status = C.CFUNCTYPE(Result, C.POINTER(Status))(api.get_status)
        self.tick_fn = C.CFUNCTYPE(Result)(api.tick)
        self.shutdown_fn = C.CFUNCTYPE(Result)(api.shutdown)
        self.window_message = C.CFUNCTYPE(Result, U64, U32, U64, I64,
                                         C.POINTER(I64))(api.window_message)
        self.user = C.WinDLL("user32", use_last_error=True)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        kernel.GetModuleHandleW.restype = W.HMODULE
        self.kernel = kernel
        instance = kernel.GetModuleHandleW(None)
        proc_type = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        self.user.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        self.user.DefWindowProcW.restype = C.c_ssize_t

        @proc_type
        def window_proc(hwnd, message, wparam, lparam):
            if self.ready:
                result = I64()
                if self.window_message(hwnd, message, wparam, lparam, C.byref(result)) == 0:
                    return result.value
            return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

        class WindowClass(C.Structure):
            _fields_ = [("style", W.UINT), ("proc", proc_type), ("class_extra", C.c_int),
                        ("window_extra", C.c_int), ("instance", W.HINSTANCE),
                        ("icon", W.HANDLE), ("cursor", W.HANDLE), ("background", W.HANDLE),
                        ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]

        self.window_proc = window_proc
        self.window_class = WindowClass(instance=instance, proc=window_proc, name="HdUEUsdviewHost")
        self.user.RegisterClassW.argtypes = [C.POINTER(WindowClass)]
        self.user.RegisterClassW.restype = W.ATOM
        self.user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
            C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
        self.user.CreateWindowExW.restype = W.HWND
        self.user.DestroyWindow.argtypes = [W.HWND]
        self.user.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
        if not self.user.RegisterClassW(C.byref(self.window_class)):
            raise C.WinError(C.get_last_error())
        self.window = self.user.CreateWindowExW(0, self.window_class.name, "hdUE / UE runtime",
            0x00CF0000, 0, 0, 640, 360, None, None, instance, None)
        if not self.window:
            raise C.WinError(C.get_last_error())
        arguments = "-UTF8Output" + (f' -abslog="{Path(log).resolve()}"' if log else "")
        project_bytes = str(Path(project).resolve()).encode("utf-8")
        desc = InitDesc(C.sizeof(InitDesc), 0, instance, self.window, project_bytes,
                        b"/Game/HdUETest", arguments.encode("utf-8"))
        self.attempted = True
        self.initializations += 1
        try:
            self.check(self.initialize(C.byref(desc)), "initialize")
            status = self.status()
            if (status.state, status.world_ready, status.rhi_ready) != (2, 1, 1):
                raise RuntimeError("UE World and rendering RHI are not ready")
            if (status.engine_major, status.engine_minor, status.engine_patch) != (5, 8, 4):
                raise RuntimeError("Expected UE 5.8.4")
            self.ready = True
        except BaseException:
            self.close()
            raise

    def assert_owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("UELibrary must run on the Qt main thread")

    def status(self):
        self.assert_owner()
        status = Status(struct_size=C.sizeof(Status))
        if self.get_status(C.byref(status)):
            raise RuntimeError("Cannot read UE status")
        return status

    def check(self, result, operation):
        if result:
            raise RuntimeError(f"{operation} failed ({result}): " +
                               self.status().message.decode("utf-8", "replace"))

    def tick(self):
        self.assert_owner()
        self.check(self.tick_fn(), "tick")
        self.ticks += 1

    def module_handles(self):
        self.assert_owner()
        return {name: self.kernel.GetModuleHandleW(name)
                for name in ("hdUE.dll", self.backend_name)}

    def close(self):
        self.assert_owner()
        if self.closed:
            return
        self.ready = False
        try:
            if self.attempted:
                self.check(self.shutdown_fn(), "shutdown")
                if self.status().state != 4:
                    raise RuntimeError("UE did not reach STOPPED")
        finally:
            self.user.DestroyWindow(self.window)
            self.user.UnregisterClassW(self.window_class.name, self.window_class.instance)
            self.closed = True
