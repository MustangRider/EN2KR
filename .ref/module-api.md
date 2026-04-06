# EN2KR 모듈 API 레퍼런스

## 의존성 그래프

```
config.py  (기반 — 의존성 없음)
  ├── audio.py
  ├── dialogs.py
  ├── pipeline.py
  ├── manager.py  ← pipeline, dialogs
  ├── overlay.py
  └── app.py      ← 모든 모듈 import
```

순환 참조 없음. `config.py`가 PySide6를 사용하는 `log_startup_error`는 함수 내 lazy import.

---

## en2kr/config.py

모든 모듈의 기반. 상수, 설정 객체, 로드/저장, 유틸.

### 상수

| 상수 | 값 | 용도 |
|------|---|------|
| `BASE_DIR` | `os.path.dirname(os.path.abspath(sys.argv[0]))` | 프로그램 루트 |
| `VAD_MODEL_DIR` | `{BASE_DIR}/models/silero_vad` | VAD 모델 파일 위치 |
| `VAD_CACHE_DIR` | `C:/temp/en2kr_cache` | torch.hub 캐시 (한글 경로 우회) |
| `LOG_DIR` | `{BASE_DIR}/logs-en2kr` | 로그 파일 디렉토리 |
| `SETTINGS_FILE` | `{BASE_DIR}/settings_en2kr_whisper_context.json` | 설정 파일 경로 |
| `WHISPER_MODEL_SIZE` | `"distil-small.en"` | Whisper 모델명 |
| `TARGET_KEYWORD` | `"CABLE Output"` | 오디오 디바이스 검색 키워드 |
| `SAMPLE_RATE` | `16000` | 오디오 샘플레이트 |
| `VAD_WINDOW_SIZE` | `512` | VAD 분석 윈도우 크기 |
| `MAX_DISPLAY_LINES` | `5` | 오버레이 기본 표시 줄 수 |
| `MAX_HISTORY_LIMIT` | `100` | 번역 히스토리 최대 보관 수 |
| `ALLOW_VAD_DOWNLOAD` | `bool` | 환경변수 `EN2KR_ALLOW_VAD_DOWNLOAD` 기반 |
| `DEFAULT_HALLUCINATION_FILTER` | `list[str]` | 환각 필터 기본값 (16개 항목) |
| `DEFAULT_GLOSSARY` | `dict[str, str]` | 게임 용어 기본값 (~50개 항목) |

### AppConfig

```python
@dataclass
class AppConfig:
    # 번역 API
    selected_api: str          # "DeepL" | "Azure"
    api_key: str               # 평문 API 키 (파일 저장 시 base64 인코딩)
    azure_region: str           # Azure 리전 (기본: "koreacentral")

    # 엔진 파라미터
    vad_threshold: float        # 0.01~1.0, VAD 감지 임계값
    silence_duration: float     # 0.2~3.0초, 침묵 대기 시간
    context_timeout: float      # 문맥 플러시 타임아웃
    context_reset_seconds: float # Whisper 문맥 리셋 시간
    min_avg_logprob: float      # Whisper 최소 평균 로그확률
    max_no_speech_prob: float   # Whisper 최대 무음 확률

    # 사용자 데이터 (field(default_factory))
    hallucination_filter: list  # 차단할 환각 텍스트 목록
    glossary: dict              # {영어: 한글} 용어집

    # 런타임 공유 상태
    running: threading.Event    # set()=실행 중, clear()=종료 신호
    audio_queue: queue.Queue    # 오디오 콜백 → VAD 워커 (maxsize=3000)
    inference_queue: queue.Queue # VAD → STT 워커 (maxsize=100)
    translation_queue: queue.Queue # STT → 번역 워커 (maxsize=100)
    filter_lock: threading.Lock # hallucination_filter 접근 동기화
    glossary_lock: threading.Lock # glossary 접근 동기화

    # 모델 (app.py에서 초기화 후 세팅)
    whisper_model: Any          # WhisperModel 인스턴스
    vad_model: Any              # Silero VAD torch.jit 모델
    device: str                 # "cpu" | "cuda" | torch_directml.device() | "xpu"
```

### 함수

```python
def init_directories() -> None
```
`VAD_MODEL_DIR`, `VAD_CACHE_DIR`, `LOG_DIR` 생성. `TORCH_HOME` 환경변수 설정. app.py 시작 시 1회 호출.

```python
def load_settings(config: AppConfig, path: str = SETTINGS_FILE) -> None
```
JSON 파일에서 config 속성으로 로드. 파일 없으면 기본값 유지. api_key는 자동 디코딩.

```python
def save_settings(config: AppConfig, path: str = SETTINGS_FILE,
                  overlay_geo=None, font_size=None,
                  line_spacing=None, max_display_lines=None) -> None
```
기존 파일을 먼저 로드 후 config 값으로 병합하여 저장. 오버레이 설정은 선택적 파라미터로 전달된 경우만 덮어씀. **데이터 유실 방지**: 파라미터 누락 시 기존 파일의 값 보존.

```python
def encode_key(key: str) -> str
def decode_key(enc_key: str) -> str
```
base64 인코딩/디코딩. 파일 저장/로드 시 사용.

```python
def log_startup_error(title: str, message: str, details: str = None) -> None
```
로그 파일 기록 + QMessageBox.critical 표시. PySide6는 함수 내 lazy import.

---

## en2kr/audio.py

오디오 디바이스 탐색 및 콜백.

```python
def find_optimal_audio_device(target_keyword: str = TARGET_KEYWORD) -> int | None
```
VB-Audio Virtual Cable 장치를 우선순위로 탐색. 반환: sounddevice 장치 인덱스 또는 None.

우선순위: DirectSound 16ch → MME 16ch → WASAPI → DirectSound <16ch → MME <16ch → WDM-KS → 기타.

```python
def make_audio_callback(config: AppConfig) -> Callable
```
`config.audio_queue`를 캡처하는 sounddevice 콜백 클로저 반환. 큐 오버플로우 시 오래된 데이터 드롭.

---

## en2kr/dialogs.py

설정 관련 Qt 다이얼로그 3종.

```python
class SetupDialog(QtWidgets.QDialog):
    def __init__(self, config: AppConfig,
                 initial_api="DeepL", initial_key="", initial_region="koreacentral")
```
API 선택(DeepL/Azure) + 키 입력 + 연결 검증. 성공 시 `config` 속성 직접 수정 + `save_settings` 호출. `self.accept()` 반환.

```python
class ReuseConfirmDialog(QtWidgets.QMessageBox):
    def __init__(self, api_type: str, api_key: str)
```
기존 설정 재사용 확인. Yes/No 반환. config 불필요.

```python
class SetupWizardDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, is_initial_setup=True)
```
VB-Cable 설치 가이드 마법사. 외부 프로그램 실행(볼륨 믹서, 사운드 제어판, 웹 브라우저)만 수행. config 불필요.

---

## en2kr/pipeline.py

백엔드 워커 스레드 함수 및 환각 추적기.

```python
class HallucinationTracker:
    def __init__(self, config: AppConfig, main_win: MainWindow)
    def check_and_ban(self, text: str) -> bool
```
- 환각 필터 목록과 대조하여 차단 여부 반환
- 3단어 이하 짧은 표현이 10분 내 5회 반복 시 자동 필터 등록
- `config.filter_lock`으로 동기화

```python
def vad_worker(config: AppConfig, main_win, overlay) -> None
```
데몬 스레드로 실행. `config.audio_queue`에서 오디오 청크를 꺼내 Silero VAD로 음성 감지. 음성 구간이 끝나면 결합된 오디오를 `config.inference_queue`에 전달. `config.running.is_set()`이 False이면 종료.

```python
def stt_worker(config: AppConfig, main_win, overlay) -> None
```
데몬 스레드로 실행. `config.inference_queue`에서 오디오를 꺼내 Whisper로 전사. 환각 체크 후 유효한 텍스트를 `overlay.recognition_signal.emit()`. `config.running.is_set()`이 False이면 종료.

---

## en2kr/manager.py

매니저 제어 창.

```python
class MainWindow(QtWidgets.QMainWindow):
    log_signal = Signal(str, str, str)      # (tag, status, msg)
    update_list_signal = Signal()            # 목록 갱신
    vad_ui_signal = Signal(bool)             # VAD 상태 표시

    def __init__(self, config: AppConfig)
```

### 주요 메서드

| 메서드 | 역할 |
|--------|------|
| `update_params()` | 슬라이더 → config.vad_threshold/silence_duration 갱신 + 저장 |
| `update_vad_ui(active)` | VAD 인디케이터 빨간불/회색 전환 |
| `append_log(tag, status, msg)` | STT 로그 UI 표시 + TRANS 로그 파일 기록 |
| `refresh_lists()` | 환각/용어집 리스트위젯 갱신 |
| `add_hall()` / `remove_hall()` | 환각 필터 추가/삭제 |
| `add_gloss()` / `remove_gloss()` | 용어집 추가/삭제 (Title Case 정규화) |
| `show_*_help()` | 각 기능 도움말 팝업 (5종) |
| `closeEvent()` | `config.running.clear()` + keyboard.unhook_all() + 설정 저장 |

---

## en2kr/overlay.py

번역 오버레이 창 + 번역 워커.

```python
class OverlayWindow(QtWidgets.QWidget):
    recognition_signal = Signal(str)            # STT 결과 수신
    vad_status_signal = Signal(bool)            # VAD 상태 수신
    update_signal = Signal(str, str, bool, str) # (en, kr, is_complete, mode)

    def __init__(self, config: AppConfig, main_win: MainWindow)
```

### 주요 메서드

| 메서드 | 역할 |
|--------|------|
| `translation_worker()` | 데몬 스레드. config.translation_queue에서 텍스트 수신 → DeepL/Azure 번역 → update_signal emit |
| `enqueue_recognition(text)` | STT 결과 수신 → 문맥 버퍼링 → translation_queue에 전달 |
| `check_context_flush()` | 타이머(500ms). 대기 중인 문맥 버퍼 강제 전송 |
| `update_ui(en, kr, is_complete, mode)` | 번역 결과를 text_lines에 추가/업데이트 → repaint |
| `toggle_edit_mode()` | F12 토글. 클릭스루 on/off, 사이즈그립 표시 |
| `set_click_through(enabled)` | win32gui로 WS_EX_TRANSPARENT 설정 |
| `change_font_size(delta)` | 편집모드에서 폰트 크기 ±2 |
| `change_max_lines(delta)` | 편집모드에서 최대 줄 수 ±1 (1~20) |
| `change_line_spacing(delta)` | 편집모드에서 줄 간격 ±2 (0~40) |
| `paintEvent()` | 영문 원문(노란색) + 한글 번역(흰색) + 4방향 그림자 렌더링 |

### 번역 파이프라인 (translation_worker 내부)
1. `config.translation_queue`에서 `(en, is_complete, mode)` 수신
2. 용어집 치환: 영어 단어를 `【한글】` 마커로 보호
3. DeepL/Azure API 호출 (3회 재시도, 0.5초 간격)
4. 마커 복원: `【한글】` → `한글`
5. `update_signal.emit(en, kr, is_complete, mode)`

---

## en2kr/app.py

진입점. 모든 모듈을 조합하여 앱을 구동.

```python
def main() -> None
```

### 실행 순서
1. CUDA 경로 추가 (EXE 환경)
2. Windows 콘솔 숨기기, stdout/stderr 처리
3. QApplication 생성
4. `config = AppConfig()` + `load_settings(config)` + `config.running.set()`
5. API 설정 루프 (ReuseConfirmDialog → SetupDialog)
6. GPU 자동 감지 (CUDA → DirectML → Intel → CPU)
7. CPU 경고 다이얼로그 (선택적)
8. Whisper 모델 로딩 → `config.whisper_model`
9. VAD 모델 로딩 → `config.vad_model` (내장 jit → hub 캐시 → 다운로드)
10. MainWindow + OverlayWindow 생성
11. 핫키 등록 (F12, =, -)
12. vad_worker, stt_worker 스레드 시작
13. 오디오 디바이스 탐색 + InputStream 시작
14. `app.exec()` 이벤트 루프
15. 종료: `config.running.clear()` → 스트림 정리 → `sys.exit()`

---

## EN2KR4GAMING.py (래퍼)

```python
from en2kr.app import main
if __name__ == "__main__":
    main()
```

빌드(PyInstaller spec), 실행(`run.bat`, `python EN2KR4GAMING.py`) 호환성 유지 목적.
