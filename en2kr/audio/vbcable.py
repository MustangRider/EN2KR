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

from .. import paths
from .endpoints import CAPTURE, RENDER, list_endpoints

log = logging.getLogger(__name__)

DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
HOME_URL = "https://vb-audio.com/Cable/"
ATTRIBUTION = ("VB-CABLE은 VB-Audio Software(Vincent Burel)의 도네이션웨어입니다. "
               "출처: www.vb-cable.com — 유용하다면 후원해 주세요.")
INPUT_NAME = "CABLE Input (VB-Audio Virtual Cable)"   # what to pick in the game


def _is_cable(name, kind):
    n = name.lower()
    return n.startswith(f"cable {kind}") and "vb-audio virtual cable" in n


def is_installed():
    has_in = any(_is_cable(e.name, "input") for e in list_endpoints(RENDER))
    has_out = any(_is_cable(e.name, "output") for e in list_endpoints(CAPTURE))
    return has_in and has_out


def default_output_is_cable():
    return any(e.is_default and "vb-audio virtual cable" in e.name.lower() for e in list_endpoints(RENDER))


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
