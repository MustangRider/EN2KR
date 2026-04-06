# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
import site

# Get torchaudio installation path
site_packages = site.getsitepackages()[0] if site.getsitepackages() else ''
torchaudio_path = os.path.join(site_packages, 'torchaudio') if site_packages else None

# Collect torchaudio components
torchaudio_hidden = collect_submodules('torchaudio')
torchaudio_datas = collect_data_files('torchaudio') if torchaudio_path and os.path.exists(torchaudio_path) else []
torchaudio_binaries = collect_dynamic_libs('torchaudio') if torchaudio_path and os.path.exists(torchaudio_path) else []

# Add explicit torchaudio path if available
if torchaudio_path and os.path.exists(torchaudio_path):
    torchaudio_datas.append((torchaudio_path, 'torchaudio'))

a = Analysis(
    ['EN2KR4GAMING.py'],
    pathex=[],
    binaries=torchaudio_binaries,
    datas=[('models', 'models')] + torchaudio_datas,
    hiddenimports=['en2kr', 'en2kr.app', 'en2kr.config', 'en2kr.audio',
        'en2kr.pipeline', 'en2kr.overlay', 'en2kr.manager', 'en2kr.dialogs',
        'torch', 'torch.cuda', 'torch._C', 'torchaudio', 'faster_whisper', 'ctranslate2', 'numpy', 'sounddevice', 'deepl', 'requests', 'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui'] + torchaudio_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EN2KR4GAMING',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['EN2KR4GAMING_1.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EN2KR4GAMING',
)
