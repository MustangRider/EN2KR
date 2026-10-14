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
# Undocumented but long-stable interface used by the Sound control panel (and by
# SoundSwitch, EarTrumpet, ...) to change the default device. Windows has no
# public API for this, so callers must verify the result and fall back to manual steps.
CLSID_PolicyConfigClient = guid("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")
IID_IPolicyConfig = guid("{F8679F50-850A-41CF-9C72-430F290290C8}")
RENDER, CAPTURE = 0, 1
DEVICE_STATE_ACTIVE = 1
ROLES = (0, 1, 2)  # eConsole, eMultimedia, eCommunications


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


def _create_enumerator():
    enum = ctypes.c_void_p()
    check(ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, 23,
                                 ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum)), "MMDeviceEnumerator")
    return enum.value


def _default_ids(enum, flow):
    ids = {}
    for role in ROLES:
        d = ctypes.c_void_p()
        if vcall(enum, 4, HRESULT, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                 flow, role, ctypes.byref(d)) == 0:
            ids[role] = _device_id(d.value)
            release(d.value)
    return ids


def _enumerate(flow):
    out = []
    enum = _create_enumerator()
    coll = None
    try:
        default_id = _default_ids(enum, flow).get(0, "")
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
        release(enum)
    return out


def _in_mta(fn, default):
    """Run a COM call on a short-lived MTA thread (the GUI thread is STA)."""
    box = {"result": default}

    def worker():
        try:
            with Apartment():
                box["result"] = fn()
        except Exception as e:  # pragma: no cover - depends on audio stack
            log.warning("Audio endpoint call failed: %s", e)

    t = threading.Thread(target=worker, name="endpoints", daemon=True)
    t.start()
    t.join(5)
    return box["result"]


def list_endpoints(flow):
    """Active endpoints for RENDER (outputs) or CAPTURE (inputs)."""
    return _in_mta(lambda: _enumerate(flow), [])


def default_ids(flow):
    """{role: endpoint id} for eConsole / eMultimedia / eCommunications."""
    def work():
        enum = _create_enumerator()
        try:
            return _default_ids(enum, flow)
        finally:
            release(enum)
    return _in_mta(work, {})


def set_default(endpoint_id, flow, roles=ROLES):
    """Make an endpoint the Windows default for the given roles. Returns True if verified."""
    def work():
        pc = ctypes.c_void_p()
        check(ole32.CoCreateInstance(ctypes.byref(CLSID_PolicyConfigClient), None, 23,
                                     ctypes.byref(IID_IPolicyConfig), ctypes.byref(pc)), "PolicyConfig")
        try:
            for role in roles:
                check(vcall(pc.value, 13, HRESULT, [ctypes.c_wchar_p, ctypes.c_int], endpoint_id, role),
                      "SetDefaultEndpoint")
        finally:
            release(pc.value)
        return True

    if not _in_mta(work, False):
        return False
    now = default_ids(flow)
    return all(now.get(r) == endpoint_id for r in roles)


def default_render_name():
    return next((e.name for e in list_endpoints(RENDER) if e.is_default), "")
