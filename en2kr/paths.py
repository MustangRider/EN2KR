"""Filesystem locations.

User data (settings, logs, transcripts) lives in %APPDATA%\\EN2KR4GAMING so the
app works when installed somewhere read-only such as Program Files.
Set EN2KR_HOME to override (used by tests).
"""
import os
import sys

from . import APP_NAME

FROZEN = getattr(sys, "frozen", False)

# Bundled read-only resources (PyInstaller extracts them under _MEIPASS).
if FROZEN:
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    EXE_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    EXE_DIR = RESOURCE_DIR

VAD_MODEL_PATH = os.path.join(RESOURCE_DIR, "models", "silero_vad", "silero_vad.onnx")
MODEL_STATS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_stats.json")
ICON_PATH = os.path.join(RESOURCE_DIR, "en2kr.ico")

# Settings file name used by v1, which stored it next to the exe/script.
LEGACY_SETTINGS_NAME = "settings_en2kr_whisper_context.json"


def app_dir():
    base = os.environ.get("EN2KR_HOME")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def settings_path():
    return os.path.join(app_dir(), "settings.json")


def log_dir():
    d = os.path.join(app_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def transcript_dir():
    d = os.path.join(app_dir(), "transcripts")
    os.makedirs(d, exist_ok=True)
    return d


def legacy_settings_candidates():
    """Places where a v1 settings file may exist, most likely first."""
    seen, out = set(), []
    for d in (EXE_DIR, os.path.dirname(os.path.abspath(sys.argv[0] or ".")), RESOURCE_DIR):
        p = os.path.normcase(os.path.join(d, LEGACY_SETTINGS_NAME))
        if p not in seen:
            seen.add(p)
            out.append(os.path.join(d, LEGACY_SETTINGS_NAME))
    return out
