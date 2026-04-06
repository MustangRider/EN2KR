# EN2KR4GAMING 모듈 분리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1,708줄 단일 파일을 7개 모듈의 `en2kr/` 패키지로 분리하고, 전역 상태를 AppConfig로 통합한다.

**Architecture:** 플랫 패키지 구조. AppConfig dataclass가 모든 공유 상태의 단일 통로. 기존 EN2KR4GAMING.py는 얇은 래퍼로 유지하여 빌드/실행 호환성 보존.

**Tech Stack:** Python 3, PySide6, faster-whisper, PyTorch, sounddevice, deepl, pywin32

**참고:** macOS에서 개발, Windows에서 실행. 테스트 실행 불가 — 각 태스크 후 정적 검증(import 구조, 시그니처 일관성).

---

## 파일 구조

| 파일 | 책임 | 원본 라인 |
|------|------|-----------|
| `en2kr/__init__.py` | 패키지 표시, 버전 | 신규 |
| `en2kr/config.py` | AppConfig, 상수, 기본값, 로드/저장, 유틸 | L33-73, L150-233, L239-242, L848-908 |
| `en2kr/dialogs.py` | SetupDialog, ReuseConfirmDialog, SetupWizardDialog | L244-501 |
| `en2kr/audio.py` | find_optimal_audio_device, audio_callback | L74-148, L1224-1230 |
| `en2kr/pipeline.py` | HallucinationTracker, vad_worker, stt_worker | L504-527, L1232-1301 |
| `en2kr/manager.py` | MainWindow | L529-924 |
| `en2kr/overlay.py` | OverlayWindow (translation_worker 포함) | L926-1221 |
| `en2kr/app.py` | main() 진입점 | L1-31, L1305-1709 |
| `EN2KR4GAMING.py` | 얇은 래퍼 | 전체 교체 |

---

### Task 1: 패키지 생성 + config.py

**Files:**
- Create: `en2kr/__init__.py`
- Create: `en2kr/config.py`

- [ ] **Step 1: `en2kr/__init__.py` 작성**

```python
"""EN2KR4GAMING - Real-time Game Voice Translation Overlay"""
__version__ = "1.0.0"
```

- [ ] **Step 2: `en2kr/config.py` 작성 — 상수 및 경로**

원본 L33-58의 상수들을 이동. `BASE_DIR` 계산, 디렉토리 생성, 환경변수 설정 포함.

```python
import sys, os, queue, threading, json, base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 경로 계산
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
VAD_MODEL_DIR = os.path.join(BASE_DIR, "models", "silero_vad")
VAD_CACHE_DIR = 'C:/temp/en2kr_cache'
LOG_DIR = os.path.join(BASE_DIR, "logs-en2kr")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings_en2kr_whisper_context.json")

# 상수
WHISPER_MODEL_SIZE = "distil-small.en"
TARGET_KEYWORD = "CABLE Output"
SAMPLE_RATE = 16000
VAD_WINDOW_SIZE = 512
MAX_DISPLAY_LINES = 5
MAX_HISTORY_LIMIT = 100
ALLOW_VAD_DOWNLOAD = os.environ.get("EN2KR_ALLOW_VAD_DOWNLOAD", "0") == "1"
```

- [ ] **Step 3: 기본값 상수 정의**

원본 L164(hallucination_filter)와 L165-233(glossary)를 `DEFAULT_*` 상수로 한 곳에만 정의. 원본 L851-878의 중복 제거.

```python
DEFAULT_HALLUCINATION_FILTER = [
    "Okay.", "Yeah.", "But.", "Thank you.", "Thanks.", ".",
    "thank you for watching", "subscribe", "watching", "subtitle",
    "...", "So.", "Oh.", "I don't know.", "That's wrong.",
    "Oh my God, I don't know."
]

DEFAULT_GLOSSARY = {
    "raider": "레이더", "rader": "레이더", "speranza": "스페란자",
    # ... (원본 L165-233의 전체 딕셔너리)
}
```

- [ ] **Step 4: AppConfig dataclass 정의**

```python
@dataclass
class AppConfig:
    # 번역 API
    selected_api: str = "DeepL"
    api_key: str = ""
    azure_region: str = "koreacentral"

    # 엔진 파라미터
    vad_threshold: float = 0.15
    silence_duration: float = 0.4
    context_timeout: float = 0.5
    context_reset_seconds: float = 3.0
    min_avg_logprob: float = -1.0
    max_no_speech_prob: float = 0.6

    # 사용자 데이터
    hallucination_filter: list = field(default_factory=lambda: list(DEFAULT_HALLUCINATION_FILTER))
    glossary: dict = field(default_factory=lambda: dict(DEFAULT_GLOSSARY))

    # 런타임 공유 상태
    running: threading.Event = field(default_factory=threading.Event)
    audio_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=3000))
    inference_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))
    translation_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=100))
    filter_lock: threading.Lock = field(default_factory=threading.Lock)
    glossary_lock: threading.Lock = field(default_factory=threading.Lock)

    # 모델 참조 (앱 초기화 후 세팅)
    whisper_model: Any = None
    vad_model: Any = None
    device: str = "cpu"
```

- [ ] **Step 5: 유틸 함수 이동**

원본 L239-242의 encode/decode_key, L60-72의 log_startup_error를 이동.

```python
def encode_key(key):
    return base64.b64encode(key.encode()).decode()

def decode_key(enc_key):
    try:
        return base64.b64decode(enc_key.encode()).decode()
    except Exception:
        return enc_key

def log_startup_error(title, message, details=None):
    from PySide6 import QtWidgets
    try:
        now = datetime.now()
        file_path = os.path.join(LOG_DIR, f"{now.strftime('%Y-%m-%d')}.txt")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"[{now.strftime('%H:%M:%S')}] {title}: {message}\n")
            if details:
                f.write(f"Details: {details}\n")
    except Exception as e:
        print(f"[ERROR] log_startup_error 파일 쓰기 실패: {e}")
    if details:
        message = f"{message}\n\n{details}"
    QtWidgets.QMessageBox.critical(None, title, message)
```

- [ ] **Step 6: load_settings / save_settings 함수**

원본 L848-908의 로직을 독립 함수로 추출. save_settings는 기존 파일 로드 후 병합하여 데이터 유실 방지.

```python
def load_settings(config: AppConfig, path: str = SETTINGS_FILE):
    """설정 파일에서 config 객체로 로드. 파일 없으면 기본값 유지."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        config.hallucination_filter = d.get("hallucinations", list(DEFAULT_HALLUCINATION_FILTER))
        loaded_glossary = d.get("glossary", {})
        config.glossary = loaded_glossary if loaded_glossary else dict(DEFAULT_GLOSSARY)
        config.vad_threshold = d.get("vad_threshold", 0.15)
        config.silence_duration = d.get("silence_duration", 0.4)
        config.selected_api = d.get("selected_api", "")
        config.api_key = decode_key(d.get("api_key", ""))
        config.azure_region = d.get("azure_region", "koreacentral")
    except Exception as e:
        print(f"[ERROR] 설정 파일 로드 실패: {e}")


def save_settings(config: AppConfig, path: str = SETTINGS_FILE,
                  overlay_geo=None, font_size=None,
                  line_spacing=None, max_display_lines=None):
    """config를 설정 파일에 저장. 기존 파일의 오버레이 설정을 보존."""
    # 기존 파일 로드 (오버레이 geo 등 보존)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            print(f"[ERROR] 기존 설정 로드 실패: {e}")

    # config에서 핵심 데이터 병합
    existing.update({
        "hallucinations": config.hallucination_filter,
        "glossary": config.glossary,
        "vad_threshold": config.vad_threshold,
        "silence_duration": config.silence_duration,
        "selected_api": config.selected_api,
        "api_key": encode_key(config.api_key),
        "azure_region": config.azure_region,
    })

    # 선택적 오버레이 설정 병합
    if overlay_geo:
        existing.update({"x": overlay_geo.x(), "y": overlay_geo.y(),
                         "w": overlay_geo.width(), "h": overlay_geo.height()})
    if font_size is not None:
        existing["font_size"] = font_size
    if line_spacing is not None:
        existing["line_spacing"] = line_spacing
    if max_display_lines is not None:
        existing["max_display_lines"] = max_display_lines

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] 설정 저장 실패: {e}")
```

- [ ] **Step 7: 디렉토리 초기화 함수**

모듈 import 시점이 아닌, app.py에서 명시적으로 호출하도록 함수화.

```python
def init_directories():
    """필요한 디렉토리 생성 및 환경변수 설정."""
    os.makedirs(VAD_MODEL_DIR, exist_ok=True)
    os.makedirs(VAD_CACHE_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.environ['TORCH_HOME'] = VAD_CACHE_DIR
```

- [ ] **Step 8: 커밋**

```bash
git add en2kr/__init__.py en2kr/config.py
git commit -m "refactor: en2kr 패키지 생성 및 config 모듈 작성

AppConfig dataclass로 전역 상태 통합. 설정 로드/저장을 독립 함수로 분리.
기본 용어집/환각 필터 중복 제거. save_settings 데이터 유실 수정."
```

---

### Task 2: dialogs.py

**Files:**
- Create: `en2kr/dialogs.py`

- [ ] **Step 1: `en2kr/dialogs.py` 작성**

원본 L246-501의 3개 다이얼로그 클래스를 이동. 변경사항:
- `global SELECTED_API, API_KEY, AZURE_REGION` → `config.selected_api`, `config.api_key`, `config.azure_region` 직접 수정
- `SetupDialog.__init__`에 `config` 파라미터 추가
- `encode_key`, `SETTINGS_FILE` → `from en2kr.config import ...`
- `ReuseConfirmDialog`, `SetupWizardDialog`는 config 불필요 (표시/외부실행만)

```python
import subprocess, webbrowser
from PySide6 import QtWidgets, QtCore
import deepl, requests

from en2kr.config import AppConfig, encode_key, save_settings, SETTINGS_FILE


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, config: AppConfig,
                 initial_api="DeepL", initial_key="", initial_region="koreacentral"):
        super().__init__()
        self.config = config
        # ... (기존 UI 코드 그대로)

    def validate_and_start(self):
        # ... (기존 검증 로직 그대로)
        if success:
            self.config.selected_api = api
            self.config.api_key = key
            self.config.azure_region = reg
            save_settings(self.config)
            self.accept()


class ReuseConfirmDialog(QtWidgets.QMessageBox):
    # 원본 L348-357 그대로 이동 (변경 없음)


class SetupWizardDialog(QtWidgets.QDialog):
    # 원본 L358-500 그대로 이동 (변경 없음)
```

- [ ] **Step 2: 커밋**

```bash
git add en2kr/dialogs.py
git commit -m "refactor: dialogs 모듈 분리

SetupDialog, ReuseConfirmDialog, SetupWizardDialog를 en2kr/dialogs.py로 이동.
SetupDialog가 AppConfig를 직접 수정하도록 변경."
```

---

### Task 3: audio.py

**Files:**
- Create: `en2kr/audio.py`

- [ ] **Step 1: `en2kr/audio.py` 작성**

원본 L74-148(find_optimal_audio_device)과 L1224-1230(audio_callback)을 이동.

변경사항:
- `find_optimal_audio_device`에 `target_keyword` 파라미터 추가 (기본값 `TARGET_KEYWORD`)
- `make_audio_callback(config)` → 클로저로 `config.audio_queue` 캡처

```python
import queue
import numpy as np
import sounddevice as sd

from en2kr.config import AppConfig, TARGET_KEYWORD


def find_optimal_audio_device(target_keyword: str = TARGET_KEYWORD):
    """원본 L74-148 그대로. TARGET_KEYWORD만 파라미터로 주입."""
    devices = sd.query_devices()
    apis = sd.query_hostapis()

    if isinstance(devices, dict):
        return None

    def get_device_priority(device_name, channels, hostapi_idx):
        # 원본 L89-122 그대로
        ...

    candidates = []
    for i in range(len(devices)):
        if devices[i]['max_input_channels'] > 0:
            name_lower = devices[i]['name'].lower()
            if (target_keyword.lower() not in name_lower
                and "vb-audio" not in name_lower
                and "virtual cable" not in name_lower
                and "cable output" not in name_lower):
                continue
            priority = get_device_priority(
                devices[i]['name'], devices[i]['max_input_channels'], devices[i]['hostapi'])
            candidates.append((i, priority, devices[i]['name'],
                             devices[i]['max_input_channels'], devices[i]['hostapi']))

    candidates.sort(key=lambda x: x[1])

    if candidates:
        selected, priority, device_name, channels, hostapi_idx = candidates[0]
        api = apis[hostapi_idx]['name']
        cable_type = ("Virtual Cable" if "virtual cable" in device_name.lower()
                      else ("Point" if "point" in device_name.lower() else "Cable"))
        print(f"[AUDIO] 최적 장치 선택: 장치#{selected} - {device_name} ({channels}ch, {cable_type}, {api})")
        return selected

    print("[ERROR] 입력 가능한 오디오 장치를 찾을 수 없습니다.")
    return None


def make_audio_callback(config: AppConfig):
    """audio_queue를 캡처하는 콜백 클로저 반환."""
    audio_queue = config.audio_queue

    def audio_callback(indata, frames, time_info, status):
        try:
            if audio_queue.qsize() > 1000:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    pass
            audio_queue.put_nowait(indata.copy().flatten())
        except queue.Full:
            pass

    return audio_callback
```

- [ ] **Step 2: 커밋**

```bash
git add en2kr/audio.py
git commit -m "refactor: audio 모듈 분리

find_optimal_audio_device와 audio_callback을 en2kr/audio.py로 이동.
audio_callback을 클로저 패턴으로 config.audio_queue 주입."
```

---

### Task 4: pipeline.py

**Files:**
- Create: `en2kr/pipeline.py`

- [ ] **Step 1: `en2kr/pipeline.py` 작성**

원본 L504-527(HallucinationTracker)과 L1232-1301(vad_worker, stt_worker)을 이동.

변경사항:
- `global hallucination_filter` → `config.hallucination_filter` + `config.filter_lock`
- `running_flag` → `config.running.is_set()`
- `GLOBAL_VAD` → `config.vad_model`
- `GLOBAL_WHISPER` → `config.whisper_model`
- `VAD_THRESHOLD` 등 상수 → `config.vad_threshold` 등 속성
- `audio_queue`/`inference_queue` → `config.audio_queue`/`config.inference_queue`

```python
import time
import queue
import numpy as np
import torch
from datetime import datetime, timedelta

from en2kr.config import AppConfig


class HallucinationTracker:
    def __init__(self, config: AppConfig, main_win):
        self.config = config
        self.main_win = main_win
        self.history = {}

    def check_and_ban(self, text):
        clean = text.strip()
        if not clean:
            return False
        with self.config.filter_lock:
            for banned in self.config.hallucination_filter:
                if banned.lower() == clean.lower():
                    return True
        if len(clean.split()) > 3:
            return False
        now = datetime.now()
        with self.config.filter_lock:
            if clean not in self.history:
                self.history[clean] = []
            self.history[clean].append(now)
            self.history[clean] = [t for t in self.history[clean]
                                   if now - t < timedelta(minutes=10)]
            if len(self.history[clean]) >= 5:
                if clean not in self.config.hallucination_filter:
                    self.config.hallucination_filter.append(clean)
                    self.main_win.update_list_signal.emit()
                    return True
        return False


def vad_worker(config: AppConfig, main_win, overlay):
    vad_acc = np.array([], dtype=np.float32)
    speech_buf = []
    in_speech = False
    last_speech = time.time()
    preroll = []

    while config.running.is_set():
        try:
            chunk = config.audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        preroll.append(chunk)
        if len(preroll) > 22:
            preroll.pop(0)
        vad_acc = np.concatenate([vad_acc, chunk])
        while len(vad_acc) >= 512:  # VAD_WINDOW_SIZE
            current = vad_acc[:512]
            vad_acc = vad_acc[512:]
            prob = config.vad_model(
                torch.from_numpy(current).float().unsqueeze(0), 16000).item()
            if prob > config.vad_threshold:
                last_speech = time.time()
                if not in_speech:
                    in_speech = True
                    speech_buf = list(preroll)
                    main_win.vad_ui_signal.emit(True)
                    overlay.vad_status_signal.emit(True)
            if in_speech:
                speech_buf.append(current)
                if (time.time() - last_speech) > config.silence_duration:
                    in_speech = False
                    main_win.vad_ui_signal.emit(False)
                    overlay.vad_status_signal.emit(False)
                    if speech_buf:
                        config.inference_queue.put(np.concatenate(speech_buf))
                        speech_buf = []


def stt_worker(config: AppConfig, main_win, overlay):
    last_end = time.time()
    last_text = ""

    while config.running.is_set():
        try:
            audio_data = config.inference_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        audio_data = audio_data - np.mean(audio_data)
        rms = np.sqrt(np.mean(audio_data ** 2))
        if rms > 1e-6:
            audio_data *= (0.1 / (rms + 1e-9))
        cond = (time.time() - last_end) < config.context_reset_seconds
        segments, _ = config.whisper_model.transcribe(
            audio_data, language="en", beam_size=5,
            condition_on_previous_text=cond)

        valid_segments = []
        for s in segments:
            txt = s.text.strip()
            if (s.no_speech_prob < config.max_no_speech_prob
                    and s.avg_logprob > config.min_avg_logprob):
                if main_win.tracker.check_and_ban(txt):
                    main_win.log_signal.emit("STT", "BLOCK", f"차단됨: {txt}")
                else:
                    valid_segments.append(txt)

        if valid_segments:
            combined_text = " ".join(valid_segments)

            if last_text:
                last_words = last_text.lower().split()
                current_words = combined_text.split()
                overlap_count = 0
                for i in range(min(5, len(last_words), len(current_words))):
                    if current_words[i].lower() in last_words[-5:]:
                        overlap_count = i + 1
                    else:
                        break
                if overlap_count > 0:
                    combined_text = " ".join(current_words[overlap_count:])
                    if not combined_text.strip():
                        continue

            overlay.recognition_signal.emit(combined_text)
            last_text = combined_text
            last_end = time.time()
            main_win.log_signal.emit("STT", "PASS", combined_text)
```

- [ ] **Step 2: 커밋**

```bash
git add en2kr/pipeline.py
git commit -m "refactor: pipeline 모듈 분리

HallucinationTracker, vad_worker, stt_worker를 en2kr/pipeline.py로 이동.
전역 변수 참조를 config 속성 참조로 전환. running_flag를 threading.Event로 변경."
```

---

### Task 5: manager.py

**Files:**
- Create: `en2kr/manager.py`

- [ ] **Step 1: `en2kr/manager.py` 작성**

원본 L529-924의 MainWindow 클래스를 이동.

변경사항:
- `__init__`에 `config: AppConfig` 파라미터 추가
- `self.load_all_settings()` 제거 (config에 이미 로드됨)
- `global VAD_THRESHOLD, SILENCE_DURATION` → `self.config.vad_threshold`, `self.config.silence_duration`
- `global hallucination_filter, glossary` → `self.config.hallucination_filter`, `self.config.glossary`
- `filter_lock`, `glossary_lock` → `self.config.filter_lock`, `self.config.glossary_lock`
- `self.save_all_settings(...)` → `save_settings(self.config, ..., overlay_geo=..., font_size=..., ...)`
- `HallucinationTracker(self)` → `HallucinationTracker(config, self)`
- `DEVICE` 참조 → `self.config.device`
- `SELECTED_API` 참조 → `self.config.selected_api`
- `running_flag = False` → `self.config.running.clear()`
- `keyboard.unhook_all()` 유지

```python
import os, keyboard
from PySide6 import QtWidgets, QtCore, QtGui
from datetime import datetime

from en2kr.config import AppConfig, save_settings, SETTINGS_FILE, LOG_DIR
from en2kr.pipeline import HallucinationTracker
from en2kr.dialogs import SetupWizardDialog


class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.Signal(str, str, str)
    update_list_signal = QtCore.Signal()
    vad_ui_signal = QtCore.Signal(bool)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle(f"EN2KR4GAMING - Manager [{config.selected_api}]")
        self.resize(1100, 850)
        self.setStyleSheet("background-color: #121212; color: #FFFFFF;")
        self.tracker = HallucinationTracker(config, self)
        self.overlay = None
        # ... (기존 UI 구성 코드 그대로, DEVICE → config.device, SELECTED_API → config.selected_api)

    def update_params(self):
        self.config.vad_threshold = self.vad_slider.value() / 100.0
        self.config.silence_duration = self.sil_slider.value() / 10.0
        self.vad_val.setText(f"{self.config.vad_threshold:.2f}")
        self.sil_val.setText(f"{self.config.silence_duration:.1f}s")
        self._save()

    def _save(self, overlay_geo=None, font_size=None,
              line_spacing=None, max_display_lines=None):
        save_settings(self.config, SETTINGS_FILE,
                     overlay_geo=overlay_geo, font_size=font_size,
                     line_spacing=line_spacing, max_display_lines=max_display_lines)

    # refresh_lists: self.config.filter_lock / self.config.hallucination_filter 사용
    # add_hall, remove_hall: self.config.filter_lock / self.config.hallucination_filter 사용
    # add_gloss, remove_gloss: self.config.glossary_lock / self.config.glossary 사용
    # append_log: LOG_DIR import로 사용
    # closeEvent: self.config.running.clear() + keyboard.unhook_all()
    # 도움말 메서드들(show_*_help): 변경 없음
```

- [ ] **Step 2: 커밋**

```bash
git add en2kr/manager.py
git commit -m "refactor: manager 모듈 분리

MainWindow를 en2kr/manager.py로 이동.
전역 변수를 config 속성으로 전환. save_all_settings를 config.save_settings로 위임."
```

---

### Task 6: overlay.py

**Files:**
- Create: `en2kr/overlay.py`

- [ ] **Step 1: `en2kr/overlay.py` 작성**

원본 L926-1221의 OverlayWindow를 이동.

변경사항:
- `__init__`에 `config: AppConfig` 파라미터 추가
- `running_flag` → `config.running.is_set()`
- `translation_queue` → `config.translation_queue`
- `SELECTED_API`, `API_KEY`, `AZURE_REGION` → `config.selected_api`, `config.api_key`, `config.azure_region`
- `glossary`, `glossary_lock` → `config.glossary`, `config.glossary_lock`
- `SETTINGS_FILE` → `from en2kr.config import SETTINGS_FILE`
- `save_all_settings(...)` → `save_settings(config, ...)` 호출
- `CONTEXT_TIMEOUT` → `config.context_timeout`
- `MAX_HISTORY_LIMIT` → `from en2kr.config import MAX_HISTORY_LIMIT`

```python
import os, json, re, time, queue, threading
import requests, deepl
from PySide6 import QtWidgets, QtCore, QtGui

try:
    import win32gui, win32con
except ImportError:
    win32gui = None

from en2kr.config import (AppConfig, save_settings, SETTINGS_FILE,
                          MAX_DISPLAY_LINES, MAX_HISTORY_LIMIT)


class OverlayWindow(QtWidgets.QWidget):
    recognition_signal = QtCore.Signal(str)
    vad_status_signal = QtCore.Signal(bool)
    update_signal = QtCore.Signal(str, str, bool, str)

    def __init__(self, config: AppConfig, main_win):
        super().__init__()
        self.config = config
        self.main_win = main_win
        # ... (기존 초기화 코드)
        # translation_queue.put → config.translation_queue.put
        # save_all_settings → save_settings(config, ...)

    def translation_worker(self):
        # running_flag → self.config.running.is_set()
        # translation_queue → self.config.translation_queue
        # SELECTED_API → self.config.selected_api
        # API_KEY → self.config.api_key
        # AZURE_REGION → self.config.azure_region
        # glossary → self.config.glossary
        # glossary_lock → self.config.glossary_lock
        # ... (기존 번역 로직 그대로)

    # paintEvent, wheelEvent, mousePressEvent 등: 변경 없음
    # set_click_through: 변경 없음
    # toggle_edit_mode: save_settings 호출로 변경
```

- [ ] **Step 2: 커밋**

```bash
git add en2kr/overlay.py
git commit -m "refactor: overlay 모듈 분리

OverlayWindow를 en2kr/overlay.py로 이동.
전역 변수를 config 속성으로 전환. translation_worker의 API 참조를 config로 변경."
```

---

### Task 7: app.py + EN2KR4GAMING.py 래퍼

**Files:**
- Create: `en2kr/app.py`
- Modify: `EN2KR4GAMING.py` (전체 교체)

- [ ] **Step 1: `en2kr/app.py` 작성**

원본 L1-31(CUDA 경로, imports)과 L1305-1709(if __name__ 블록)을 `main()` 함수로 재구성.

```python
import sys, os, time, threading
import keyboard

def main():
    # CUDA 경로 추가 (EXE 환경) — 원본 L14-23 그대로
    if getattr(sys, 'frozen', False):
        cuda_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
        if os.path.exists(cuda_base):
            cuda_versions = [d for d in os.listdir(cuda_base)
                           if os.path.isdir(os.path.join(cuda_base, d))]
            cuda_versions.sort(reverse=True)
            for version in cuda_versions:
                cuda_path = os.path.join(cuda_base, version, "bin")
                if os.path.exists(cuda_path):
                    os.environ['PATH'] = cuda_path + os.pathsep + os.environ.get('PATH', '')
                    break

    import torch
    import sounddevice as sd
    from PySide6 import QtWidgets, QtCore
    from faster_whisper import WhisperModel
    import webbrowser

    from en2kr.config import (
        AppConfig, load_settings, save_settings, init_directories,
        log_startup_error,
        SETTINGS_FILE, VAD_MODEL_DIR, VAD_CACHE_DIR,
        WHISPER_MODEL_SIZE, SAMPLE_RATE, ALLOW_VAD_DOWNLOAD
    )
    from en2kr.dialogs import SetupDialog, ReuseConfirmDialog, SetupWizardDialog
    from en2kr.audio import find_optimal_audio_device, make_audio_callback
    from en2kr.pipeline import vad_worker, stt_worker
    from en2kr.manager import MainWindow
    from en2kr.overlay import OverlayWindow

    # 디렉토리 초기화
    init_directories()

    # Windows 콘솔 숨기기 — 원본 L1306-1314 그대로
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32')
        user32 = ctypes.WinDLL('user32')
        hWnd = kernel32.GetConsoleWindow()
        if hWnd:
            user32.ShowWindow(hWnd, 0)

    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')

    app = QtWidgets.QApplication(sys.argv)

    # config 생성 및 설정 로드
    config = AppConfig()
    load_settings(config)
    config.running.set()  # 실행 상태로 설정

    # 초기 로딩 대화상자 — 원본 L1325-1337 그대로
    init_dialog = QtWidgets.QProgressDialog("프로그램을 초기화하는 중입니다...", None, 0, 0)
    init_dialog.setWindowTitle("EN2KR4GAMING")
    init_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    init_dialog.setCancelButton(None)
    init_dialog.setMinimumDuration(0)
    init_dialog.setRange(0, 0)
    init_dialog.setFixedSize(400, 100)
    init_dialog.show()
    for _ in range(10):
        app.processEvents()
    time.sleep(0.2)

    # API 설정 루프 — 원본 L1340-1413
    # config.selected_api / config.api_key를 사용하여 동일 로직
    setup_complete = False
    while not setup_complete:
        saved_api = config.selected_api
        saved_key = config.api_key
        saved_region = config.azure_region
        use_saved = False

        if saved_api and saved_key:
            init_dialog.setLabelText("저장된 API 설정을 확인하는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)

            confirm = ReuseConfirmDialog(saved_api, saved_key)
            init_dialog.hide()
            result = confirm.exec()
            if result == QtWidgets.QMessageBox.StandardButton.Yes:
                use_saved = True
                setup_complete = True
            elif result == QtWidgets.QMessageBox.StandardButton.No:
                init_dialog.show()
                init_dialog.setLabelText("새로운 API 설정을 준비하는 중...")
                for _ in range(5):
                    app.processEvents()
                time.sleep(0.1)
            else:
                init_dialog.close()
                sys.exit()

        if not use_saved:
            init_dialog.setLabelText("API 설정 창을 여는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)

            setup = SetupDialog(config, saved_api or "DeepL", saved_key, saved_region)
            init_dialog.hide()
            if setup.exec() == QtWidgets.QDialog.Accepted:
                if (config.selected_api and config.api_key
                        and config.selected_api.strip() and config.api_key.strip()):
                    setup_complete = True
                else:
                    init_dialog.show()
                    init_dialog.setLabelText("설정 재시도 중...")
                    for _ in range(5):
                        app.processEvents()
                    time.sleep(0.1)
                    QtWidgets.QMessageBox.warning(
                        None, "알림", "설정이 제대로 저장되지 않았습니다. 다시 시도해주세요.")
            else:
                init_dialog.close()
                sys.exit()

    init_dialog.close()
    print(f"[SYSTEM] 최종 API: '{config.selected_api}', Key Set: {bool(config.api_key)}")

    # 모델 로딩 — 원본 L1420-1650
    loading_dialog = QtWidgets.QProgressDialog("AI 모델을 로드 중입니다...", None, 0, 3)
    loading_dialog.setWindowTitle("로딩 중")
    loading_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    loading_dialog.setCancelButton(None)
    loading_dialog.setMinimumDuration(0)
    loading_dialog.setValue(0)
    loading_dialog.show()
    app.processEvents()

    # GPU 감지 — 원본 L1433-1530 그대로
    config.device = "cpu"
    if torch.cuda.is_available():
        config.device = "cuda"
        print(f"[GPU] CUDA: {torch.version.cuda}")
    else:
        try:
            import torch_directml
            config.device = torch_directml.device()
            print("[GPU] DirectML 활성화")
        except ImportError:
            try:
                import intel_extension_for_pytorch
                config.device = "xpu"
                print("[GPU] Intel Extension 활성화")
            except ImportError:
                print("[GPU] GPU 없음 — CPU 모드")

    if config.device == "cpu":
        # GPU 경고 다이얼로그 — 원본 L1456-1530 그대로
        loading_dialog.hide()
        # ... (원본 코드 그대로 복사)
        loading_dialog.show()

    # Whisper 로딩
    loading_dialog.setLabelText(f"Whisper 모델 로드 중... (Device: {str(config.device).upper()})")
    loading_dialog.setValue(1)
    app.processEvents()
    try:
        config.whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=config.device,
            compute_type="float16" if config.device == "cuda" else "int8"
        )
    except Exception as e:
        log_startup_error("Whisper 모델 로드 실패",
                         "Whisper 모델을 초기화하지 못했습니다.", str(e))
        sys.exit(1)

    # VAD 로딩 — 원본 L1549-1647 그대로
    loading_dialog.setLabelText("VAD 모델 로드 중...")
    loading_dialog.setValue(2)
    app.processEvents()
    # ... (원본의 3단계 로딩 로직 그대로, GLOBAL_VAD → config.vad_model)

    loading_dialog.setValue(3)
    loading_dialog.close()

    # 윈도우 생성
    mw = MainWindow(config)
    mw.show()
    ov = OverlayWindow(config, mw)
    mw.overlay = ov

    # 핫키
    keyboard.add_hotkey('f12', ov.toggle_edit_mode)
    keyboard.add_hotkey('=', lambda: ov.change_font_size(2))
    keyboard.add_hotkey('-', lambda: ov.change_font_size(-2))

    # 워커 스레드
    threading.Thread(target=vad_worker, args=(config, mw, ov), daemon=True).start()
    threading.Thread(target=stt_worker, args=(config, mw, ov), daemon=True).start()

    # 오디오 스트림 — 원본 L1668-1708 그대로
    idx = find_optimal_audio_device()
    if idx is None:
        # 설정 마법사 → 재시도 — 원본 L1672-1690 그대로
        ...

    try:
        stream = sd.InputStream(
            device=idx, channels=1, samplerate=SAMPLE_RATE,
            callback=make_audio_callback(config))
        stream.start()
        exit_code = app.exec()
    except Exception as e:
        log_startup_error("오디오 스트림 오류",
                         "오디오 입력 스트림을 시작하지 못했습니다.", str(e))
        exit_code = 1
    finally:
        config.running.clear()
        if 'stream' in locals():
            stream.stop()
            stream.close()
        sys.exit(exit_code)
```

- [ ] **Step 2: `EN2KR4GAMING.py`를 얇은 래퍼로 교체**

```python
#!/usr/bin/env python3
"""EN2KR4GAMING - Real-time Game Voice Translation Overlay"""
from en2kr.app import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 커밋**

```bash
git add en2kr/app.py EN2KR4GAMING.py
git commit -m "refactor: app 모듈 + EN2KR4GAMING.py 래퍼 완성

main() 진입점을 en2kr/app.py로 이동.
EN2KR4GAMING.py는 얇은 래퍼로 교체 — 빌드/실행 호환성 유지."
```

---

### Task 8: 정리 및 검증

**Files:**
- Modify: `EN2KR4GAMING.spec:21` — hiddenimports에 `en2kr` 패키지 추가
- Review: 전체 모듈 import 일관성 검증

- [ ] **Step 1: spec 파일에 패키지 추가**

PyInstaller가 `en2kr` 패키지를 확실히 포함하도록 hiddenimports에 추가.

```python
# EN2KR4GAMING.spec L25 수정
hiddenimports=[
    'en2kr', 'en2kr.app', 'en2kr.config', 'en2kr.audio',
    'en2kr.pipeline', 'en2kr.overlay', 'en2kr.manager', 'en2kr.dialogs',
    'torch', 'torch.cuda', ...  # 기존 항목 유지
]
```

- [ ] **Step 2: import 일관성 검증**

각 모듈의 import 관계가 순환 참조 없이 DAG를 형성하는지 확인:

```
config.py      ← (의존성 없음, PySide6만 lazy import)
audio.py       ← config
dialogs.py     ← config
pipeline.py    ← config
manager.py     ← config, pipeline, dialogs
overlay.py     ← config
app.py         ← config, dialogs, audio, pipeline, manager, overlay
```

순환 참조 없음 확인.

- [ ] **Step 3: 최종 커밋**

```bash
git add EN2KR4GAMING.spec
git commit -m "refactor: PyInstaller spec에 en2kr 패키지 hiddenimports 추가"
```
