"""Enumerate Windows audio endpoints with their friendly names (always fresh,
unlike PortAudio's device list which is cached at initialisation)."""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
from dataclasses import dataclass

from .com import GUID, HRESULT, Apartment, check, guid, ole32, release, vcall

log = logging.getLogger(__name__)

CLSID_MMDeviceEnumerator = guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
RENDER, CAPTURE = 0, 1
DEVICE_STATE_ACTIVE = 1


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.WORD), ("r2", wt.WORD), ("r3", wt.WORD),
                ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


PKEY_Device_FriendlyName = _PropertyKey(guid("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)


@dataclass
class Endpoint:
    id: str
    name: str
    is_default: bool


def _device_id(dev):
    p = ctypes.c_wchar_p()
    if vcall(dev, 5, HRESULT, [ctypes.POINTER(ctypes.c_wchar_p)], ctypes.byref(p)) != 0:
        return ""
    try:
        return p.value or ""
    finally:
        ole32.CoTaskMemFree(p)


def _friendly_name(dev):
    store = ctypes.c_void_p()
    if vcall(dev, 4, HRESULT, [wt.DWORD, ctypes.POINTER(ctypes.c_void_p)], 0, ctypes.byref(store)) != 0:
        return ""
    try:
        pv = _PropVariant()
        vcall(store, 5, HRESULT, [ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)],
              ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(pv))
        name = ctypes.wstring_at(pv.ptr) if pv.vt == 31 and pv.ptr else ""  # VT_LPWSTR
        ole32.PropVariantClear(ctypes.byref(pv))
        return name
    finally:
        release(store.value)


def _enumerate(flow):
    out = []
    enum = ctypes.c_void_p()
    check(ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, 23,
                                 ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum)), "MMDeviceEnumerator")
    coll = default = None
    try:
        d = ctypes.c_void_p()
        default_id = ""
        if vcall(enum, 4, HRESULT, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                 flow, 0, ctypes.byref(d)) == 0:
            default = d.value
            default_id = _device_id(default)
        c = ctypes.c_void_p()
        check(vcall(enum, 3, HRESULT, [ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)],
                    flow, DEVICE_STATE_ACTIVE, ctypes.byref(c)), "EnumAudioEndpoints")
        coll = c.value
        n = wt.UINT()
        vcall(coll, 3, HRESULT, [ctypes.POINTER(wt.UINT)], ctypes.byref(n))
        for i in range(n.value):
            dev = ctypes.c_void_p()
            if vcall(coll, 4, HRESULT, [wt.UINT, ctypes.POINTER(ctypes.c_void_p)], i, ctypes.byref(dev)) != 0:
                continue
            try:
                dev_id = _device_id(dev)
                out.append(Endpoint(dev_id, _friendly_name(dev), dev_id == default_id))
            finally:
                release(dev.value)
    finally:
        release(coll)
        release(default)
        release(enum.value)
    return out


def list_endpoints(flow):
    """Active endpoints for RENDER (outputs) or CAPTURE (inputs)."""
    box = {"result": []}

    def worker():
        try:
            with Apartment():
                box["result"] = _enumerate(flow)
        except Exception as e:  # pragma: no cover - depends on audio stack
            log.warning("Endpoint enumeration failed: %s", e)

    t = threading.Thread(target=worker, name="endpoints", daemon=True)
    t.start()
    t.join(5)
    return box["result"]


def default_render_name():
    return next((e.name for e in list_endpoints(RENDER) if e.is_default), "")
