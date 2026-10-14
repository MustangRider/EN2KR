"""Minimal ctypes COM helpers (no comtypes/pywin32 dependency)."""
import ctypes
import ctypes.wintypes as wt

HRESULT = ctypes.c_long
ole32 = ctypes.WinDLL("ole32")
kernel32 = ctypes.WinDLL("kernel32")

COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
S_OK, S_FALSE = 0, 1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __eq__(self, other):
        return isinstance(other, GUID) and bytes(self) == bytes(other)

    def __hash__(self):
        return hash(bytes(self))


def guid(s):
    g = GUID()
    hr = ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    if hr != S_OK:
        raise ValueError(f"bad GUID {s}")
    return g


class ComError(OSError):
    def __init__(self, what, hr):
        self.hresult = hr & 0xFFFFFFFF
        super().__init__(f"{what} failed (hr=0x{self.hresult:08X})")


def check(hr, what):
    if hr < 0:
        raise ComError(what, hr)
    return hr


def vcall(ptr, index, restype, argtypes, *args):
    """Call method #index of the COM interface at ``ptr``."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(ptr, *args)


def query_interface(ptr, iid):
    out = ctypes.c_void_p()
    hr = vcall(ptr, 0, HRESULT, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
               ctypes.byref(iid), ctypes.byref(out))
    return out.value if hr == S_OK else None


def release(ptr):
    if ptr:
        vcall(ptr, 2, wt.ULONG, [])


class Apartment:
    """CoInitializeEx(MTA) for the current thread; tolerant of an existing STA."""

    def __enter__(self):
        hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        self._uninit = hr in (S_OK, S_FALSE)
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            raise ComError("CoInitializeEx", hr)
        return self

    def __exit__(self, *exc):
        if self._uninit:
            ole32.CoUninitialize()
