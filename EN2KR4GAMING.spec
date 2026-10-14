# -*- mode: python ; coding: utf-8 -*-

import os

datas = [
    ('model', 'model'),
    ('models/silero_vad/silero_vad.jit', 'models/silero_vad'),
]

if os.path.isdir('models/whisper_onnx'):
    datas.append(('models/whisper_onnx', 'models/whisper_onnx'))

a = Analysis(
    ['EN2KR4GAMING.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['torch', 'torch.cuda', 'torch._C', 'faster_whisper', 'ctranslate2', 'numpy', 'sounddevice', 'deepl', 'requests', 'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'optimum', 'optimum.onnxruntime', 'transformers', 'onnxruntime', 'onnxruntime.capi', 'tiktoken'],
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
    a.binaries,
    a.zipfiles,
    a.datas,
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
    icon=['en2kr.ico'],
)
