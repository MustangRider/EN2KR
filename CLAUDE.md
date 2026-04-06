# EN2KR4GAMING — 실시간 게임 음성 번역 오버레이

## 운영 환경
- **타겟 플랫폼**: Windows 전용 (pywin32, CUDA, VB-Audio 의존)
- **개발 환경**: macOS에서 리모트 Claude Code로 코드 편집·커밋 → Windows에서 실행·테스트
- **CLAUDE.md 편집 규칙**: 수정 전후 규칙 수 대조 검증 / 토큰 절약 압축 문체 유지
- **작업 완료 후 반영 검토**: 변경 시 CLAUDE.md 업데이트 필요 여부 검토

## 프로젝트 목적
**게임 내 영어 음성 → 한국어 자막 실시간 오버레이**

- 파이프라인: 게임 오디오(VB-Audio) → Silero VAD → Whisper STT → DeepL/Azure 번역 → PySide6 오버레이
- 패키지 구조: `EN2KR4GAMING.py`(래퍼) → `en2kr/` 패키지 (모듈 분리 진행 중)

## 프로젝트 구조

| 파일/폴더 | 내용 |
|-----------|------|
| `EN2KR4GAMING.py` | 얇은 래퍼 (`from en2kr.app import main`) |
| `en2kr/` | 메인 패키지 (config, audio, pipeline, overlay, manager, dialogs, app) |
| `EN2KR4GAMING.spec` | PyInstaller EXE 빌드 설정 |
| `requirements.txt` | Python 의존성 |
| `models/silero_vad/` | VAD 모델 (`silero_vad.jit`) |
| `settings_en2kr_whisper_context.sample.json` | 설정 템플릿 |
| `run.bat` | Windows 실행 스크립트 |

## 코드 구조 (en2kr/ 패키지)

| 모듈 | 내용 |
|------|------|
| `config.py` | AppConfig dataclass, 상수, 기본값, 로드/저장, 유틸 |
| `audio.py` | 오디오 디바이스 탐색, 콜백 클로저 |
| `pipeline.py` | HallucinationTracker, vad_worker, stt_worker |
| `manager.py` | MainWindow (매니저 제어 패널) |
| `overlay.py` | OverlayWindow + translation_worker |
| `dialogs.py` | SetupDialog, ReuseConfirmDialog, SetupWizardDialog |
| `app.py` | main() 진입점 — 앱 초기화, 모델 로딩, 스트림 시작 |

## 기술 스택
- **GUI**: PySide6
- **STT**: faster-whisper (distil-small.en)
- **VAD**: Silero VAD (torch.jit)
- **번역**: DeepL API / Azure Translator
- **오디오**: sounddevice + VB-Audio Virtual Cable
- **빌드**: PyInstaller

## 레퍼런스
- **모듈 API 상세 (함수 시그니처·의존성·데이터 흐름)**: `.ref/module-api.md`
- **모듈 분리 설계 스펙**: `docs/superpowers/specs/2026-04-07-module-separation-design.md`
- **모듈 분리 구현 계획**: `docs/superpowers/plans/2026-04-07-module-separation.md`

## 주의사항
- **Windows 전용 API**: `pywin32`, `sounddevice` 디바이스 열거 등 — macOS에서 import 실패 정상
- **한글 경로 이슈**: `TORCH_HOME=C:/temp/en2kr_cache`로 우회 (한글 사용자명 경로 에러 방지)
- **CUDA 폴백**: NVIDIA CUDA → DirectML → Intel Arc → CPU 순서 자동 감지
- **VAD 모델**: `models/silero_vad/silero_vad.jit` 번들 — 자동 다운로드 기본 비활성 (`EN2KR_ALLOW_VAD_DOWNLOAD=1`로 활성화)
- **환각 필터**: Whisper가 무음에서 생성하는 오탐("Okay.", "Yeah." 등) 자동 차단
- **설정 파일**: `settings_en2kr_whisper_context.json` — API 키는 base64 인코딩 저장
- **EXE 빌드**: `EN2KR4GAMING.spec`으로 PyInstaller 빌드, torchaudio 번들링 포함

## 메모리 시스템
- **구조**: `.claude/projects/.../memory/` — MEMORY.md(인덱스) + 개별 파일
- **금지**: 코드에서 읽을 수 있는 내용 미저장 / CLAUDE.md 중복 저장 금지
