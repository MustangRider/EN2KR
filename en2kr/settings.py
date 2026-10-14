"""Persistent settings.

- Stored as JSON in %APPDATA%\\EN2KR4GAMING\\settings.json.
- Read with utf-8-sig so a BOM added by an external editor does not break it.
- A file that cannot be parsed is moved aside (never silently overwritten).
- Saves are atomic (temp file + os.replace) and always write every key,
  so one caller cannot drop keys owned by another.
- API keys are encrypted with DPAPI on disk.
- A v1 settings file found next to the exe is migrated on first run.
"""
import copy
import json
import logging
import os
import threading
import time

from . import paths, secret_store

log = logging.getLogger(__name__)

DEFAULT_GLOSSARY = {
    "raider": "레이더", "rader": "레이더", "speranza": "스페란자",
    "Stella Montis": "스텔라 몬티스", "blue gate": "블루 게이트",
    "Kettle": "케틀", "Rattler": "래틀러", "Arpeggio": "아르페지오",
    "Tempest": "템페스트", "Bettina": "베티나", "Ferro": "페로",
    "Renegade": "레니게이드", "Aphelion": "아펠리온", "Stitcher": "스티처",
    "Bobcat": "밥캣", "Il Toro": "일토로", "Vulcano": "볼카노",
    "Hairpin": "헤어핀", "Burletta": "부를레타", "Venator": "베나토르",
    "Anvil": "앤빌", "Equalizer": "이퀄라이저", "Jupiter": "쥬피터",
    "Osprey": "오스프레이", "Torrente": "토렌테", "Hullcracker": "헐크래커",
    "Photoelectric Cloak": "광전자 망토", "Snap Hook": "스냅훅",
    "Zipline": "짚라인", "Barricade Kit": "바리케이드 키트",
    "Raider Hatch Key": "레이더 해치 키", "Binoculars": "쌍안경",
    "Shield Recharger": "실드 충전기", "Bandage": "붕대",
    "Blaze Grenade": "화염 수류탄", "Jolt Mine": "충격 지뢰",
    "Wolfpack": "울프팩", "Showstopper": "쇼스토퍼",
    "Heavy Fuse Grenade": "중량 신관 수류탄", "Deadline": "데드라인",
    "lure grenade": "유인 수류탄", "Trigger Grenade": "트리거 수류탄",
    "Pulse Mine": "펄스 지뢰", "Snitch": "스니치", "Wasp": "와스프",
    "Hornet": "호넷", "Rocketeer": "로켓티어", "Pop": "팝",
    "Fireball": "파이어볼", "Tick": "틱", "Turret": "터렛",
    "Sentinel": "센티넬", "Shredder": "슈레더", "Surveyor": "서베이어",
    "Leaper": "리퍼", "Bastion": "바스티온", "Bombardier": "봄바디어",
    "Spotter": "스포터", "Queen": "퀸", "Matriarch": "마트리아크",
    "snap blast grenade": "스냅 블래스트", "Defib": "재세동기",
    "goop": "아이템", "blueprint": "설계도",
}

DEFAULT_HALLUCINATIONS = [
    "Okay.", "Yeah.", "But.", "Thank you.", "Thanks.", ".", "thank you for watching",
    "subscribe", "watching", "subtitle", "...", "So.", "Oh.", "I don't know.",
    "That's wrong.", "Oh my God, I don't know.",
]

# English -> English expansions applied before machine translation.
DEFAULT_SLANG = {
    "gg": "good game", "wp": "well played", "nade": "grenade", "nades": "grenades",
    "rez": "revive", "brb": "be right back", "omw": "on my way", "idk": "I don't know",
    "nvm": "never mind", "tbh": "to be honest", "imo": "in my opinion",
    "np": "no problem", "ty": "thank you", "thx": "thanks", "pls": "please", "plz": "please",
}

OPENAI_PRESETS = {
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    "ollama": {"label": "Ollama (로컬)", "base_url": "http://localhost:11434/v1"},
    "custom": {"label": "직접 입력", "base_url": ""},
}

DEFAULTS = {
    "version": 2,
    "audio": {"mode": "process", "process_name": "PioneerGame.exe", "device_name": ""},
    "vad": {"threshold": 0.3, "silence_sec": 0.5, "preroll_sec": 0.3,
            "max_utterance_sec": 12.0, "min_speech_sec": 0.25},
    "stt": {"mode": "english", "model": "auto", "language": "en",
            "auto_languages": ["en", "ko", "de", "fr", "es", "ru", "pl"],
            "skip_korean": True, "int8": False, "device": "auto", "beam_size": 5,
            "use_context": True},
    "translate": {
        "order": [],
        "providers": {
            "deepl": {"key": ""},
            "azure": {"key": "", "region": "koreacentral"},
            "google": {"key": ""},
            "openai": {"preset": "gemini", "base_url": OPENAI_PRESETS["gemini"]["base_url"],
                       "model": "", "key": ""},
        },
        "cache_size": 500,
        "use_slang": True,
    },
    "glossary": DEFAULT_GLOSSARY,
    "slang": DEFAULT_SLANG,
    "hallucinations": DEFAULT_HALLUCINATIONS,
    "overlay": {"x": 100, "y": 100, "w": 800, "h": 250, "font_family": "Malgun Gothic",
                "font_size": 18, "line_spacing": 12, "max_lines": 5, "show_original": True,
                "fade_sec": 0, "bg_opacity": 0},
    "hotkeys": {"edit_mode": "f12"},
    "logging": {"save_transcripts": True, "retention_days": 14},
    "ui": {"cpu_warning_dismissed": False, "onboarding_done": False, "int8_unsupported": False},
}

# Collections the user edits as a whole: a saved value replaces the default.
_REPLACE_KEYS = {"glossary", "slang", "hallucinations", "order", "auto_languages"}


def _merge(defaults, saved):
    out = copy.deepcopy(defaults)
    for k, v in saved.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict) and k not in _REPLACE_KEYS:
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _map_secrets(data, fn):
    for prov in data.get("translate", {}).get("providers", {}).values():
        if isinstance(prov, dict) and "key" in prov:
            prov["key"] = fn(prov.get("key") or "")
    return data


def migrate_v1(d):
    """Convert a v1 settings dict to the v2 layout."""
    out = copy.deepcopy(DEFAULTS)
    if isinstance(d.get("hallucinations"), list):
        out["hallucinations"] = [str(x) for x in d["hallucinations"]]
    if isinstance(d.get("glossary"), dict) and d["glossary"]:
        out["glossary"] = {str(k): str(v) for k, v in d["glossary"].items()}
    if "vad_threshold" in d:
        out["vad"]["threshold"] = min(max(float(d["vad_threshold"]), 0.01), 0.99)
    if "silence_duration" in d:
        out["vad"]["silence_sec"] = min(max(float(d["silence_duration"]), 0.2), 3.0)
    api = str(d.get("selected_api") or "").strip().lower()
    if api in ("deepl", "azure"):
        key = secret_store.decode_legacy(d.get("api_key", ""))
        out["translate"]["providers"][api]["key"] = key
        if key:
            out["translate"]["order"] = [api]
    if d.get("azure_region"):
        out["translate"]["providers"]["azure"]["region"] = d["azure_region"]
    ov = out["overlay"]
    for old, new in (("x", "x"), ("y", "y"), ("w", "w"), ("h", "h"), ("font_size", "font_size"),
                     ("line_spacing", "line_spacing"), ("max_display_lines", "max_lines")):
        if isinstance(d.get(old), (int, float)):
            ov[new] = int(d[old])
    if d.get("ignore_cuda_warning"):
        out["ui"]["cpu_warning_dismissed"] = True
    return out


class Settings:
    def __init__(self, path=None):
        self.path = path or paths.settings_path()
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self.load_error = None      # message shown to the user if the file was unreadable
        self.migrated_from = None   # v1 file path if migrated this run

    # ---------- load / save ----------
    def load(self):
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8-sig") as f:
                        saved = json.load(f)
                    if not isinstance(saved, dict):
                        raise ValueError("top-level JSON is not an object")
                    self._data = _map_secrets(_merge(DEFAULTS, saved), secret_store.decrypt)
                    return self
                except (OSError, ValueError) as e:
                    backup = f"{self.path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
                    try:
                        os.replace(self.path, backup)
                    except OSError:
                        backup = None
                    self.load_error = f"설정 파일을 읽지 못해 기본값으로 시작합니다.\n원인: {e}"
                    if backup:
                        self.load_error += f"\n원본은 보존됨: {backup}"
                    log.error("Settings unreadable (%s); backup=%s", e, backup)
                    self._data = copy.deepcopy(DEFAULTS)
                    return self
            for legacy in paths.legacy_settings_candidates():
                if not os.path.exists(legacy):
                    continue
                try:
                    with open(legacy, "r", encoding="utf-8-sig") as f:
                        self._data = migrate_v1(json.load(f))
                    self.migrated_from = legacy
                    log.info("Migrated v1 settings from %s", legacy)
                    self.save()
                    break
                except (OSError, ValueError) as e:
                    log.warning("Could not migrate v1 settings %s: %s", legacy, e)
            return self

    def save(self):
        with self._lock:
            data = _map_secrets(copy.deepcopy(self._data), secret_store.encrypt)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    # ---------- access ----------
    def get(self, dotted, default=None):
        with self._lock:
            node = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted, value):
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = copy.deepcopy(value)

    def section(self, name):
        return self.get(name, {})

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)
