# -*- mode: python ; coding: utf-8 -*-
# Build (from the repo root, inside the project venv):
#   .venv\Scripts\pyinstaller EN2KR4GAMING.spec --noconfirm
# Output: dist\EN2KR4GAMING\EN2KR4GAMING.exe (+ _internal). Folder build on
# purpose: a one-file exe would unpack ~2 GB to %TEMP% on every launch.
import glob
import os
import site

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

site_packages = next(p for p in site.getsitepackages() if p.lower().endswith("site-packages"))

# cuBLAS / cuDNN / NVRTC for ctranslate2 (users need only an NVIDIA driver).
# en2kr.gpu adds _internal\nvidia\*\bin to the DLL search path at startup.
binaries = []
for bin_dir in glob.glob(os.path.join(site_packages, "nvidia", "*", "bin")):
    dest = os.path.relpath(bin_dir, site_packages)
    binaries += [(dll, dest) for dll in glob.glob(os.path.join(bin_dir, "*.dll"))]
# ctranslate2 loads every DLL next to its package at import time.
binaries += collect_dynamic_libs("ctranslate2")

datas = [
    ("models/silero_vad/silero_vad.onnx", "models/silero_vad"),
]
datas += collect_data_files("faster_whisper")
icon = None
if os.path.exists("en2kr.ico"):
    datas.append(("en2kr.ico", "."))
    icon = ["en2kr.ico"]

a = Analysis(
    ["EN2KR4GAMING.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # av (PyAV): only used by faster-whisper to decode files; it bundles GPL codecs (see en2kr/stt.py)
    excludes=["torch", "torchaudio", "tensorflow", "matplotlib", "tkinter", "IPython", "pytest", "av",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtPdf"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EN2KR4GAMING",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EN2KR4GAMING",
)
