"""Help the user install VB-CABLE (VB-Audio donationware).

The package's own licence forbids folding it into another program's installer
without the author's agreement, so this app never bundles or silently
installs it. On the user's request it downloads the official package from
vb-audio.com and opens VB-Audio's own setup program (with its own UI/EULA).
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import platform
import zipfile

import requests

from dataclasses import dataclass, field

from .. import paths
from .endpoints import CAPTURE, RENDER, default_ids, list_endpoints, set_default

log = logging.getLogger(__name__)

DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
HOME_URL = "https://vb-audio.com/Cable/"
ATTRIBUTION = ("VB-CABLE은 VB-Audio Software(Vincent Burel)의 도네이션웨어입니다. "
               "출처: www.vb-cable.com — 유용하다면 후원해 주세요.")
INPUT_NAME = "CABLE Input (VB-Audio Virtual Cable)"   # what to pick in the game


def is_cable_endpoint(name):
    """The main 2-channel VB-CABLE endpoint (not the 16ch one).

    Matched by the device part of the name: right after installation, before
    a reboot, Windows can show the render side as "스피커(VB-Audio Virtual
    Cable)" instead of "CABLE Input(...)".
    """
    n = name.lower()
    return "vb-audio virtual cable" in n and "16ch" not in n.replace(" ", "")


def cable_render_name():
    """Current name of the endpoint to pick in the game, or "" if missing."""
    return next((e.name for e in list_endpoints(RENDER) if is_cable_endpoint(e.name)), "")


def is_installed():
    return bool(cable_render_name()) and any(is_cable_endpoint(e.name) for e in list_endpoints(CAPTURE))


def _is_vb(name):
    return "vb-audio virtual cable" in name.lower()


def _cable_roles(flow):
    """Roles (console/multimedia/communications) whose default device is VB-CABLE."""
    names = {e.id: e.name for e in list_endpoints(flow)}
    return [role for role, dev in default_ids(flow).items() if _is_vb(names.get(dev, ""))]


def default_output_is_cable():
    return bool(_cable_roles(RENDER))


def default_input_is_cable():
    """Windows often makes CABLE Output the default microphone when VB-CABLE is installed."""
    return bool(_cable_roles(CAPTURE))


def snapshot_defaults():
    """Current non-VB-CABLE default devices, saved before running the setup."""
    snap = {}
    for flow in (RENDER, CAPTURE):
        names = {e.id: e.name for e in list_endpoints(flow)}
        snap[str(flow)] = {str(role): dev for role, dev in default_ids(flow).items()
                           if dev in names and not _is_vb(names[dev])}
    return snap


@dataclass
class FixResult:
    changed: list = field(default_factory=list)      # human-readable "마이크 → Blue Snowball"
    failed: list = field(default_factory=list)       # flows that could not be fixed ("출력"/"마이크")
    need_choice: dict = field(default_factory=dict)  # flow -> [Endpoint] when the target is ambiguous


FLOW_LABEL = {RENDER: "출력", CAPTURE: "마이크"}


def fix_cable_defaults(snapshot=None, chosen=None):
    """Point every default role that is VB-CABLE back to a real device.

    Target per role: the user's choice, else the device saved in ``snapshot``
    before installation, else the only non-VB-CABLE device of that kind. If
    several are possible the caller asks the user (``need_choice``).
    """
    result = FixResult()
    for flow in (RENDER, CAPTURE):
        eps = {e.id: e for e in list_endpoints(flow)}
        current = default_ids(flow)
        bad = [r for r, dev in current.items() if dev in eps and _is_vb(eps[dev].name)]
        if not bad:
            continue
        real = [e for e in eps.values() if not _is_vb(e.name)]
        saved = (snapshot or {}).get(str(flow), {})
        targets = {}
        for role in bad:
            dev = (chosen or {}).get(flow) or saved.get(str(role))
            if dev not in eps or _is_vb(eps[dev].name):
                dev = real[0].id if len(real) == 1 else None
            if dev is None:
                break
            targets.setdefault(dev, []).append(role)
        else:
            ok = True
            for dev, roles in targets.items():
                if set_default(dev, flow, roles):
                    result.changed.append(f"{FLOW_LABEL[flow]} → {eps[dev].name}")
                else:
                    ok = False
            if not ok:
                result.failed.append(FLOW_LABEL[flow])
            continue
        if real:
            result.need_choice[flow] = real
        else:
            result.failed.append(FLOW_LABEL[flow])
    return result


MANUAL_STEPS = {
    "출력": "Win + R → mmsys.cpl → <b>재생</b> 탭 → 헤드셋(스피커) 우클릭 → '기본 장치로 설정'과 '기본 통신 장치로 설정'",
    "마이크": "Win + R → mmsys.cpl → <b>녹음</b> 탭 → 사용하는 마이크 우클릭 → '기본 장치로 설정'과 '기본 통신 장치로 설정'",
}


def download(progress=None):
    """Download and extract the official package; return the setup exe path."""
    folder = os.path.join(paths.app_dir(), "vbcable")
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, os.path.basename(DOWNLOAD_URL))
    with requests.get(DOWNLOAD_URL, stream=True, timeout=(5, 30)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(archive + ".part", "wb") as f:
            for block in r.iter_content(64 * 1024):
                f.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
    os.replace(archive + ".part", archive)
    exe = "VBCABLE_Setup_x64.exe" if platform.machine().endswith("64") else "VBCABLE_Setup.exe"
    with zipfile.ZipFile(archive) as z:
        if exe not in z.namelist():
            raise RuntimeError(f"패키지에 {exe}가 없습니다. VB-Audio 사이트에서 직접 설치하세요.")
        target = os.path.join(folder, "pack")
        z.extractall(target)
    return os.path.join(target, exe)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
                ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY), ("dwHotKey", wt.DWORD),
                ("hIconOrMonitor", wt.HANDLE), ("hProcess", wt.HANDLE)]


def run_setup(exe_path):
    """Run VB-Audio's setup elevated and wait. Returns exit code, or None if UAC was declined."""
    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe_path
    info.lpDirectory = os.path.dirname(exe_path)
    info.nShow = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:  # ERROR_CANCELLED: user said no to UAC
            return None
        raise OSError(f"설치 프로그램을 실행하지 못했습니다 (오류 {err})")
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)
