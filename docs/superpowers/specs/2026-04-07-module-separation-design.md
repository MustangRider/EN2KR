# EN2KR4GAMING 모듈 분리 설계

## 목표
1,708줄 단일 파일(`EN2KR4GAMING.py`)을 역할별 모듈로 분리하고, 전역 상태를 AppConfig 객체로 통합한다. 동작 변경 없이 구조만 개선.

## 범위
- 파일 분리 + 전역 상태 정리 + 기본 품질 개선
- 번역 파이프라인 로직, UI 레이아웃, 빌드 설정은 변경하지 않음

## 패키지 구조

```
EN2KR4GAMING.py              # 얇은 래퍼: from en2kr.app import main; main()
en2kr/
  __init__.py                # 버전 정보만
  app.py                     # main() — QApp 생성, GPU 감지, 모델 로딩, 오디오 스트림, 핫키
  config.py                  # AppConfig dataclass + 상수 + 로드/저장 + 유틸
  audio.py                   # find_optimal_audio_device(), audio_callback()
  pipeline.py                # vad_worker(), stt_worker(), HallucinationTracker
  overlay.py                 # OverlayWindow (translation_worker 포함)
  manager.py                 # MainWindow
  dialogs.py                 # SetupDialog, ReuseConfirmDialog, SetupWizardDialog
```

## AppConfig dataclass

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

    # 사용자 데이터 (field(default_factory=...) 사용)
    hallucination_filter: list    # 기본값: DEFAULT_HALLUCINATION_FILTER
    glossary: dict                # 기본값: DEFAULT_GLOSSARY

    # 런타임 공유 상태
    running: threading.Event      # set() 상태가 실행 중, clear()로 종료 신호
    audio_queue: queue.Queue      # maxsize=3000
    inference_queue: queue.Queue  # maxsize=100
    translation_queue: queue.Queue # maxsize=100
    filter_lock: threading.Lock
    glossary_lock: threading.Lock

    # 모델 참조 (앱 초기화 후 세팅)
    whisper_model: object = None
    vad_model: object = None
    device: str = "cpu"
```

### 설계 결정
- `running_flag: bool` → `threading.Event`: `event.is_set()`으로 체크, `event.wait(timeout=)`으로 대기 가능하여 워커 종료가 깔끔
- 큐와 락을 config에 포함: 모듈 간 공유 상태의 유일한 통로로 역할
- 모델 참조도 config에 포함: `app.py`에서 로딩 후 세팅, 워커에서 참조

## 모듈별 책임

### config.py
- `AppConfig` dataclass 정의
- 상수: `SAMPLE_RATE`, `VAD_WINDOW_SIZE`, `MAX_DISPLAY_LINES`, `MAX_HISTORY_LIMIT`, `WHISPER_MODEL_SIZE`, `TARGET_KEYWORD`, `BASE_DIR`, `SETTINGS_FILE`, `LOG_DIR`, `VAD_MODEL_DIR`, `VAD_CACHE_DIR`, `ALLOW_VAD_DOWNLOAD`
- 기본값: `DEFAULT_HALLUCINATION_FILTER`, `DEFAULT_GLOSSARY` (한 곳에만 정의)
- 함수: `load_settings(path) -> AppConfig`, `save_settings(config, path, overlay_geo=None, font_size=None, line_spacing=None, max_display_lines=None)`
- 유틸: `encode_key()`, `decode_key()`, `log_startup_error()`
- `save_settings`는 기존 파일을 먼저 로드 후 병합하여 데이터 유실 방지

### audio.py
- `find_optimal_audio_device()` — 현재 로직 그대로 이동
- `audio_callback(config)` — 클로저 또는 partial로 config.audio_queue 접근

### pipeline.py
- `vad_worker(config, main_win, overlay)` — config에서 vad_model, audio_queue, inference_queue, running 참조
- `stt_worker(config, main_win, overlay)` — config에서 whisper_model, inference_queue, running 참조
- `HallucinationTracker(config, main_win)` — config에서 hallucination_filter, filter_lock 참조

### overlay.py
- `OverlayWindow(config, main_win)` — config 전체 참조
- `translation_worker`는 OverlayWindow 내부 메서드로 유지 (현재와 동일)
- 시그널/슬롯 구조 변경 없음

### manager.py
- `MainWindow(config)` — config 전체 참조
- 슬라이더 변경 시 `config.vad_threshold`, `config.silence_duration` 직접 수정
- `save_all_settings` → `config.save_settings()` 호출로 위임
- `load_all_settings` → `config.load_settings()` 호출로 위임 (생성 시점에 이미 로드됨)

### dialogs.py
- `SetupDialog(config)` — API 검증 성공 시 config 속성 직접 수정 + save_settings 호출
- `ReuseConfirmDialog(api_type, api_key)` — 변경 없음 (표시용)
- `SetupWizardDialog(parent, is_initial_setup)` — 변경 없음 (외부 프로그램 실행만)

### app.py
- `main()` 함수:
  1. Windows 콘솔 숨기기, stdout/stderr 처리
  2. QApplication 생성
  3. `config = load_settings(SETTINGS_FILE)` (또는 기본값으로 생성)
  4. API 설정 루프 (SetupDialog / ReuseConfirmDialog)
  5. GPU 감지, Whisper 로딩 → `config.whisper_model`
  6. VAD 로딩 → `config.vad_model`
  7. MainWindow, OverlayWindow 생성
  8. 핫키 등록 (keyboard)
  9. 워커 스레드 시작
  10. 오디오 디바이스 탐색 + 스트림 시작
  11. `app.exec()`
  12. 종료 처리

## 품질 개선

| 항목 | 현재 | 변경 |
|------|------|------|
| bare `except: pass` | 6곳 이상 | `except Exception as e: print(f"[ERROR] {context}: {e}")` |
| 설정 저장 데이터 유실 | 파라미터 누락 시 기존값 소실 | 항상 기존 파일 로드 후 병합 |
| `running_flag` bool | 스레드 종료 대기 불가 | `threading.Event` — `clear()` + `wait()` |
| 기본 용어집 중복 | 전역 변수(L165) + load_all_settings(L851) | `DEFAULT_GLOSSARY` 한 곳 |

## 변경하지 않는 것
- 번역 파이프라인 로직 (용어집 【】 치환, 환각 감지, 재시도)
- UI 레이아웃, 스타일시트, 위젯 구조
- PyInstaller spec (진입점 `EN2KR4GAMING.py` 유지)
- 오디오 디바이스 우선순위 로직
- `run.bat`, `auto_push.ps1`
- VAD 모델 로딩 순서 (내장 → 캐시 → hub → 다운로드)

## 마이그레이션 전략
1. `en2kr/` 패키지 생성, `config.py`부터 작성 (다른 모듈의 기반)
2. 의존성 없는 모듈부터 이동: `dialogs.py` → `audio.py` → `pipeline.py`
3. UI 모듈 이동: `manager.py` → `overlay.py`
4. `app.py` 작성 (기존 `if __name__ == "__main__"` 블록 재구성)
5. `EN2KR4GAMING.py`를 얇은 래퍼로 교체
6. 기존 `EN2KR4GAMING.py` 원본은 git 히스토리에 보존됨
