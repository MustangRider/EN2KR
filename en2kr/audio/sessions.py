"""Discover which processes are playing audio, and pick a capture target.

Uses the Windows audio session API (the same data the volume mixer shows) and a
Toolhelp process snapshot. Neither opens a handle to the game process.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import threading
from dataclasses import dataclass

from .com import GUID, HRESULT, Apartment, check, guid, ole32, query_interface, release, vcall

log = logging.getLogger(__name__)

CLSID_MMDeviceEnumerator = guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = guid("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = guid("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
IID_IAudioMeterInformation = guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
CLSCTX_ALL = 23
E_RENDER, DEVICE_STATE_ACTIVE = 0, 1
SESSION_ACTIVE = 1


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    name: str


@dataclass
class AudioApp:
    pid: int
    name: str
    active: bool
    peak: float

    @property
    def label(self):
        return self.name


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def list_processes():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wt.HANDLE(-1).value):
        return []
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append(ProcInfo(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def process_alive(pid, name=None):
    for p in list_processes():
        if p.pid == pid:
            return name is None or p.name.lower() == name.lower()
    return False


def _enumerate_sessions():
    """Return {pid: (active, peak)} across all active render endpoints."""
    result = {}
    enum = ctypes.c_void_p()
    check(ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                 ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum)),
          "CoCreateInstance(MMDeviceEnumerator)")
    coll = ctypes.c_void_p()
    try:
        check(vcall(enum, 3, HRESULT, [ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)],
                    E_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(coll)), "EnumAudioEndpoints")
        count = wt.UINT()
        vcall(coll, 3, HRESULT, [ctypes.POINTER(wt.UINT)], ctypes.byref(count))
        for i in range(count.value):
            dev = ctypes.c_void_p()
            if vcall(coll, 4, HRESULT, [wt.UINT, ctypes.POINTER(ctypes.c_void_p)], i, ctypes.byref(dev)) != 0:
                continue
            mgr = ctypes.c_void_p()
            try:
                if vcall(dev, 3, HRESULT, [ctypes.POINTER(GUID), wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                         ctypes.byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, ctypes.byref(mgr)) != 0:
                    continue
                _collect_sessions(mgr, result)
            finally:
                release(mgr.value)
                release(dev.value)
    finally:
        release(coll.value)
        release(enum.value)
    return result


def _collect_sessions(mgr, result):
    senum = ctypes.c_void_p()
    if vcall(mgr, 5, HRESULT, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(senum)) != 0:
        return
    try:
        n = ctypes.c_int()
        vcall(senum, 3, HRESULT, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(n))
        for j in range(n.value):
            ctl = ctypes.c_void_p()
            if vcall(senum, 4, HRESULT, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], j, ctypes.byref(ctl)) != 0:
                continue
            ctl2 = meter = None
            try:
                ctl2 = query_interface(ctl, IID_IAudioSessionControl2)
                if not ctl2 or vcall(ctl2, 15, HRESULT, []) == 0:  # IsSystemSoundsSession -> S_OK
                    continue
                pid = wt.DWORD()
                vcall(ctl2, 14, HRESULT, [ctypes.POINTER(wt.DWORD)], ctypes.byref(pid))
                state = ctypes.c_int()
                vcall(ctl, 3, HRESULT, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(state))
                peak = ctypes.c_float(0.0)
                meter = query_interface(ctl, IID_IAudioMeterInformation)
                if meter:
                    vcall(meter, 3, HRESULT, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(peak))
                active, old_peak = result.get(pid.value, (False, 0.0))
                result[pid.value] = (active or state.value == SESSION_ACTIVE, max(old_peak, peak.value))
            finally:
                release(meter)
                release(ctl2)
                release(ctl.value)
    finally:
        release(senum.value)


def list_audio_apps():
    """Processes that own an audio output session (like the volume mixer)."""
    box = {}

    def worker():
        try:
            with Apartment():
                box["sessions"] = _enumerate_sessions()
        except Exception as e:  # pragma: no cover - depends on audio stack
            log.warning("Audio session enumeration failed: %s", e)
            box["sessions"] = {}

    t = threading.Thread(target=worker, name="audio-sessions", daemon=True)
    t.start()
    t.join(5)
    sessions = box.get("sessions", {})
    names = {p.pid: p.name for p in list_processes()}
    own = os.getpid()
    apps = [AudioApp(pid, names.get(pid, f"PID {pid}"), active, peak)
            for pid, (active, peak) in sessions.items() if pid and pid != own]
    apps.sort(key=lambda a: (not a.active, -a.peak, a.name.lower()))
    return apps


def is_descendant(pid, ancestor, procs):
    parents = {p.pid: p.ppid for p in procs}
    seen = set()
    while pid in parents and pid not in seen:
        seen.add(pid)
        pid = parents[pid]
        if pid == ancestor:
            return True
    return False


def resolve_target_pid(process_name, procs=None, audio=None):
    """Pick the PID to capture for an exe name.

    audio: {pid: AudioApp} from list_audio_apps(). Prefers the instance that is
    playing sound right now, then any instance owning an audio session, then the
    root-most instance (capture includes child processes, e.g. a launcher's game).
    """
    procs = procs if procs is not None else list_processes()
    audio = audio or {}
    want = process_name.lower()
    matches = [p for p in procs if p.name.lower() == want]
    if not matches:
        return None

    def rank(p):
        a = audio.get(p.pid)
        playing = bool(a and a.active and a.peak > 0.001)
        return (not playing, a is None, -(a.peak if a else 0.0))

    best = min(matches, key=rank)
    if best.pid in audio:
        return best.pid
    pids = {p.pid for p in matches}
    roots = [p for p in matches if p.ppid not in pids]
    return (roots or matches)[0].pid


def playing_elsewhere(process_name, current_pid, procs=None, audio=None):
    """PID that is playing sound while the captured process is silent, if any.

    Candidates: other instances of the same exe, or descendants of the captured
    process. Process loopback only includes child processes that already existed
    when capture started, so a game spawned later by its launcher must be
    targeted directly.
    """
    audio = audio or {}
    cur = audio.get(current_pid)
    if cur and cur.active and cur.peak > 0.001:
        return None
    procs = procs if procs is not None else list_processes()
    want = process_name.lower()
    for a in audio.values():
        if a.pid == current_pid or not (a.active and a.peak > 0.001):
            continue
        if a.name.lower() == want or is_descendant(a.pid, current_pid, procs):
            return a.pid
    return None
