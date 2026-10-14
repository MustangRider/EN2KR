"""GPU discovery for faster-whisper (ctranslate2) without torch.

ctranslate2 needs cuBLAS 12 and cuDNN 9 at runtime. They come from the
nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels (bundled into the release
build), so users only need an NVIDIA driver - not the CUDA Toolkit.
"""
import ctypes
import glob
import logging
import os
import site
import sys
from dataclasses import dataclass

from . import paths

log = logging.getLogger(__name__)
_dll_dirs_added = False
_preloaded = []  # keep handles alive

# Loaded by full path before ctranslate2 starts, so a later load by name reuses them.
_PRELOAD = ("cublasLt64_12.dll", "cublas64_12.dll")


def _ignore_cuda_toolkit():
    """Hide an installed CUDA Toolkit from this process.

    With CUDA_PATH set, the CUDA libraries were taken from the Toolkit instead of
    the bundled ones. On the RTX 5060 test PC that meant cuBLAS 12.1 (older than
    the GPU) at 0.64 s per 9.9 s clip vs 0.26 s with the bundled cuBLAS 12.9.
    """
    for key in [k for k in os.environ if k.upper().startswith("CUDA_PATH")]:
        os.environ.pop(key, None)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(p for p in parts if "nvidia gpu computing toolkit" not in p.lower())


def setup_cuda_dll_paths():
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True
    roots = [paths.RESOURCE_DIR] if paths.FROZEN else []
    try:
        roots += site.getsitepackages()
    except AttributeError:  # pragma: no cover
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    dirs, seen = [], set()
    for root in roots:
        for d in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            key = os.path.normcase(os.path.abspath(d))
            if key not in seen:
                seen.add(key)
                dirs.append(d)
    if not dirs:
        return  # no bundled CUDA libraries: leave the system setup alone
    _ignore_cuda_toolkit()
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except OSError:
            continue
        # ctranslate2 loads cuDNN lazily with plain LoadLibrary, which only honours PATH.
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        log.debug("CUDA DLL dir: %s", d)
    for name in _PRELOAD:
        for d in dirs:
            full = os.path.join(d, name)
            if os.path.exists(full):
                try:
                    _preloaded.append(ctypes.WinDLL(full))
                except OSError as e:
                    log.warning("Could not preload %s: %s", full, e)
                break


def loaded_library_path(name):
    """Full path of an already-loaded DLL (diagnostics), or ''."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    handle = k32.GetModuleHandleW(name)
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    k32.GetModuleFileNameW(ctypes.c_void_p(handle), buf, 1024)
    return buf.value


def cuda_device_count():
    setup_cuda_dll_paths()
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count()
    except Exception as e:
        log.warning("CUDA probe failed: %s", e)
        return 0


@dataclass
class GpuInfo:
    name: str
    total_mb: int
    used_mb: int

    @property
    def free_mb(self):
        return self.total_mb - self.used_mb


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


_nvml = None
_nvml_handle = None


def gpu_info(index=0):
    """Current VRAM usage of the GPU via NVML (ships with the NVIDIA driver)."""
    global _nvml, _nvml_handle
    if _nvml is False:
        return None
    try:
        if _nvml is None:
            dll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")
            _nvml = ctypes.WinDLL(dll)
            if _nvml.nvmlInit_v2() != 0:
                raise OSError("nvmlInit failed")
            handle = ctypes.c_void_p()
            if _nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) != 0:
                raise OSError("no GPU at index")
            _nvml_handle = handle
        mem = _NvmlMemory()
        if _nvml.nvmlDeviceGetMemoryInfo(_nvml_handle, ctypes.byref(mem)) != 0:
            return None
        name = ctypes.create_string_buffer(96)
        _nvml.nvmlDeviceGetName(_nvml_handle, name, 96)
        return GpuInfo(name.value.decode(errors="replace"), mem.total // 2**20, mem.used // 2**20)
    except OSError:
        _nvml = False  # don't retry every call
        return None
    except (AttributeError, TypeError):
        return None
