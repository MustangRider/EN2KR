"""
EN2KR4GAMING - Configuration module
Foundation module: constants, defaults, AppConfig dataclass, settings I/O.
"""

import os
import sys
import json
import queue
import threading
import base64
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime

# ================= 1. 전역 설정 및 경로 =================

BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# VAD 모델 경로 (프로그램 폴더 내부)
VAD_MODEL_DIR = os.path.join(BASE_DIR, "models", "silero_vad")

# 한글 경로 에러 방지를 위한 고정 경로 설정 (폴백용)
VAD_CACHE_DIR = 'C:/temp/en2kr_cache'

LOG_DIR = os.path.join(BASE_DIR, "logs-en2kr")

SETTINGS_FILE = os.path.join(BASE_DIR, "settings_en2kr_whisper_context.json")
WHISPER_MODEL_SIZE = "distil-small.en"
TARGET_KEYWORD = "CABLE Output"
SAMPLE_RATE = 16000
VAD_WINDOW_SIZE = 512
MAX_DISPLAY_LINES = 5
MAX_HISTORY_LIMIT = 100
ALLOW_VAD_DOWNLOAD = os.environ.get("EN2KR_ALLOW_VAD_DOWNLOAD", "0") == "1"

# ================= 2. 기본값 =================

DEFAULT_HALLUCINATION_FILTER = [
    "Okay.",
    "Yeah.",
    "But.",
    "Thank you.",
    "Thanks.",
    ".",
    "thank you for watching",
    "subscribe",
    "watching",
    "subtitle",
    "...",
    "So.",
    "Oh.",
    "I don't know.",
    "That's wrong.",
    "Oh my God, I don't know.",
]

DEFAULT_GLOSSARY = {
    "raider": "레이더",
    "rader": "레이더",
    "speranza": "스페란자",
    "Stella Montis": "스텔라 몬티스",
    "blue gate": "블루 게이트",
    "Kettle": "케틀",
    "Rattler": "래틀러",
    "Arpeggio": "아르페지오",
    "Tempest": "템페스트",
    "Bettina": "베티나",
    "Ferro": "페로",
    "Renegade": "레니게이드",
    "Aphelion": "아펠리온",
    "Stitcher": "스티처",
    "Bobcat": "밥캣",
    "Il Toro": "일토로",
    "Vulcano": "볼카노",
    "Hairpin": "헤어핀",
    "Burletta": "부를레타",
    "Venator": "베나토르",
    "Anvil": "앤빌",
    "Equalizer": "이퀄라이저",
    "Jupiter": "쥬피터",
    "Osprey": "오스프레이",
    "Torrente": "토렌테",
    "Hullcracker": "헐크래커",
    "Photoelectric Cloak": "광전자 망토",
    "Snap Hook": "스냅훅",
    "Zipline": "짚라인",
    "Barricade Kit": "바리케이드 키트",
    "Raider Hatch Key": "레이더 해치 키",
    "Binoculars": "쌍안경",
    "Shield Recharger": "실드 충전기",
    "Bandage": "붕대",
    "Blaze Grenade": "화염 수류탄",
    "Jolt Mine": "충격 지뢰",
    "Wolfpack": "울프팩",
    "Showstopper": "쇼스토퍼",
    "Heavy Fuse Grenade": "중량 신관 수류탄",
    "Deadline": "데드라인",
    "lure grenade": "유인 수류탄",
    "Trigger Grenade": "트리거 수류탄",
    "Pulse Mine": "펄스 지뢰",
    "Snitch": "스니치",
    "Wasp": "와스프",
    "Hornet": "호넷",
    "Rocketeer": "로켓티어",
    "Pop": "팝",
    "Fireball": "파이어볼",
    "Tick": "틱",
    "Turret": "터렛",
    "Sentinel": "센티넬",
    "Shredder": "슈레더",
    "Surveyor": "서베이어",
    "Leaper": "리퍼",
    "Bastion": "바스티온",
    "Bombardier": "봄바디어",
    "Spotter": "스포터",
    "Queen": "퀸",
    "Matriarch": "마트리아크",
    "raders": "레이더",
    "raiders": "레이더",
    "snap blast grenade": "스냅 블래스트",
    "Defib": "재세동기",
    "goop": "아이템",
    "goops": "아이템들",
    "blueprint": "설계도",
}

# ================= 3. AppConfig dataclass =================

@dataclass
class AppConfig:
    selected_api: str = "DeepL"
    api_key: str = ""
    azure_region: str = "koreacentral"
    vad_threshold: float = 0.15
    silence_duration: float = 0.4
    context_timeout: float = 0.5
    context_reset_seconds: float = 3.0
    min_avg_logprob: float = -1.0
    max_no_speech_prob: float = 0.6
    hallucination_filter: list = field(default_factory=lambda: list(DEFAULT_HALLUCINATION_FILTER))
    glossary: dict = field(default_factory=lambda: dict(DEFAULT_GLOSSARY))
    running: threading.Event = field(default_factory=threading.Event)
    audio_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=3000))
    inference_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))
    translation_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))
    filter_lock: threading.Lock = field(default_factory=threading.Lock)
    glossary_lock: threading.Lock = field(default_factory=threading.Lock)
    whisper_model: Any = None
    vad_model: Any = None
    device: str = "cpu"

# ================= 4. 유틸리티 함수 =================

def encode_key(key: str) -> str:
    """Base64-encode an API key for storage."""
    return base64.b64encode(key.encode()).decode()


def decode_key(enc_key: str) -> str:
    """Base64-decode a stored API key; returns the raw string on failure."""
    try:
        return base64.b64decode(enc_key.encode()).decode()
    except Exception:
        return enc_key


def log_startup_error(title: str, message: str, details: str = None) -> None:
    """Log an error to the daily log file and show a QMessageBox."""
    try:
        now = datetime.now()
        file_path = os.path.join(LOG_DIR, f"{now.strftime('%Y-%m-%d')}.txt")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"[{now.strftime('%H:%M:%S')}] {title}: {message}\n")
            if details:
                f.write(f"Details: {details}\n")
    except Exception:
        pass
    if details:
        message = f"{message}\n\n{details}"
    # Lazy import to avoid requiring PySide6 at module level
    from PySide6 import QtWidgets
    QtWidgets.QMessageBox.critical(None, title, message)

# ================= 5. 설정 함수 =================

def load_settings(config: AppConfig, path: str = SETTINGS_FILE) -> None:
    """
    Load settings from JSON into config.
    Missing file: keep AppConfig defaults unchanged.
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        config.hallucination_filter = d.get("hallucinations", config.hallucination_filter)
        loaded_glossary = d.get("glossary", {})
        config.glossary = loaded_glossary if loaded_glossary else dict(DEFAULT_GLOSSARY)
        config.vad_threshold = d.get("vad_threshold", config.vad_threshold)
        config.silence_duration = d.get("silence_duration", config.silence_duration)
        config.selected_api = d.get("selected_api", config.selected_api)
        config.api_key = decode_key(d.get("api_key", ""))
        config.azure_region = d.get("azure_region", config.azure_region)
    except Exception as e:
        print(f"[ERROR] 설정 파일 로드 실패: {e}")


def save_settings(
    config: AppConfig,
    path: str = SETTINGS_FILE,
    overlay_geo=None,
    font_size=None,
    line_spacing=None,
    max_display_lines=None,
) -> None:
    """
    Save settings to JSON.
    Loads the existing file first so that keys not managed by config
    (e.g. overlay geometry, font_size) are preserved when the optional
    parameters are None.
    """
    # Start from existing file contents to avoid data loss
    d: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}

    # Merge config values (always overwrite these keys)
    d.update({
        "hallucinations": config.hallucination_filter,
        "glossary": config.glossary,
        "vad_threshold": config.vad_threshold,
        "silence_duration": config.silence_duration,
        "selected_api": config.selected_api,
        "api_key": encode_key(config.api_key),
        "azure_region": config.azure_region,
    })

    # Optional overlay / UI params — only update when explicitly provided
    if overlay_geo is not None:
        d.update({
            "x": overlay_geo.x(),
            "y": overlay_geo.y(),
            "w": overlay_geo.width(),
            "h": overlay_geo.height(),
        })
    if font_size is not None:
        d["font_size"] = font_size
    if line_spacing is not None:
        d["line_spacing"] = line_spacing
    if max_display_lines is not None:
        d["max_display_lines"] = max_display_lines

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] 설정 저장 실패: {e}")

# ================= 6. 디렉토리 초기화 =================

def init_directories() -> None:
    """
    Create required directories and set TORCH_HOME.
    Must be called explicitly by app.py; NOT executed on import.
    """
    os.makedirs(VAD_MODEL_DIR, exist_ok=True)
    os.makedirs(VAD_CACHE_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.environ['TORCH_HOME'] = VAD_CACHE_DIR
