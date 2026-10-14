# EN2KR4GAMING

실시간 게임 음성 번역 오버레이. 게임(또는 원하는 프로그램)의 소리만 캡처해서
Whisper로 받아 적고, 번역 API로 한국어로 옮겨 게임 화면 위에 자막으로 띄웁니다.

## v2에서 달라진 점

- **VB-Cable 불필요**: Windows 프로세스 루프백 캡처로 선택한 프로그램의 소리만 직접 받습니다.
  게임 소리는 평소처럼 헤드셋으로 들립니다. (OBS '애플리케이션 오디오 캡처'와 같은 방식, 게임 프로세스는 건드리지 않음)
- **음성 채팅만 번역 (선택)**: 게임의 '음성 채팅 출력 장치'를 VB-CABLE로 보내면 효과음·음악 없이
  음성 채팅만 번역하고, 음성 채팅은 앱이 헤드셋으로 다시 들려줍니다. VB-CABLE이 없으면 앱이 공식 설치 프로그램을 받아 실행해 줍니다.
- **프로그램 안에서 바로 설정**: 메인 창의 '번역할 프로그램'에서 소리를 내는 앱을 고르면 끝. 고른 프로그램은 기억되고, 게임을 재시작하면 자동으로 다시 연결됩니다.
- **인식 엔진 선택**: 영어 전용 / 다국어·언어 고정 / 다국어·자동 감지 중에서 고르고, 모델 크기를 **실측 VRAM·속도와 현재 GPU 여유 VRAM**을 보며 정합니다.
- **클라우드 인식 (v2.1)**: GPU가 없거나 VRAM이 부족하면 '인식 엔진'에서 실행 위치를 **클라우드 · Groq**로 바꿉니다. VRAM을 쓰지 않고 문장당 약 0.3~0.9초에 받아 적습니다 (무료 API 키, 하루 2,000문장 · 분당 20문장).
- **한국어 음성 건너뛰기 (모든 모드)**: 한국인 파티원이나 한국어 방송은 번역하지 않습니다. 영어 전용 엔진은 한국어를 영어로 바꿔 받아 적기 때문에, 작은 언어 판별 모델(tiny)을 함께 써서 걸러냅니다.
- **번역 API 여러 개 + 자동 전환**: DeepL, Azure, Google Cloud Translation, OpenAI 호환 LLM(Gemini·Groq·OpenRouter·Ollama). 한도 초과·오류 시 다음 API로 넘어갑니다.
- **용어집·슬랭**: 어떤 번역 API를 써도 동작합니다 (긴 용어 우선, 복수형 자동 처리).
- **배포 크기**: torch 제거(음성 감지를 ONNX로 전환)로 약 4GB → 약 2GB.
- v1 버그 수정: 이전 문장 단어 중복, 미완성 문장 이중 번역, 설정 유실(BOM·덮어쓰기), 마이크 오선택, 작업 스레드 무음 사망, 단축키 스레드 크래시, 오버레이 긴 문장 잘림 등.

## 요구 사항

- Windows 10 22H2 / Windows 11 (프로세스 오디오 캡처 사용, Windows 10 19045에서 확인)
- NVIDIA GPU 권장 (그래픽 드라이버만 있으면 됨, CUDA Toolkit 불필요). GPU가 없으면 클라우드 인식(Groq)을 쓰세요. CPU 인식도 되지만 느립니다.
- 번역 API 키 1개 이상
- (음성 채팅만 번역 모드) VB-CABLE

| 번역 API | 무료 한도 | 비고 |
|---|---|---|
| DeepL | 월 50만 자 | 품질 좋음 |
| Azure Translator (F0) | 월 200만 자 | 지역(region) 입력 필요 |
| Google Cloud Translation | 월 50만 자 | 결제 계정 연결 필요 |
| Gemini/Groq 등 (OpenAI 호환) | 서비스마다 다름 | 슬랭·문맥에 강함, 느림. 무료 티어는 입력이 학습에 쓰일 수 있음 |

무료 한도는 바뀔 수 있으니 각 서비스의 요금 페이지에서 확인하세요.

## 사용법

1. 실행 후 '번역 API'에서 키 등록 (여러 개 등록 시 위에서부터 순서대로 사용)
2. 게임 실행 → '번역할 프로그램'에서 게임 선택
3. 필요하면 '인식 엔진'에서 종류·크기 선택 (GPU가 없거나 게임이 VRAM을 많이 쓰면 '클라우드 · Groq')
4. **F12**로 오버레이 편집 모드: 드래그로 이동, 오른쪽 아래 모서리로 크기 조절, `=`/`-` 글자 크기, Ctrl+휠 줄 수, Shift+휠 줄 간격
5. 게임은 '테두리 없는 창 모드(Borderless)'로 실행하세요. 독점 전체 화면에서는 오버레이가 보이지 않습니다.

### 클라우드 인식 (Groq)

1. 키 발급 (무료, 카드 등록 없음): https://console.groq.com/keys 에서 가입·로그인 → **Create API Key** → 이름 입력 → `gsk_`로 시작하는 키 복사.
   키는 만들 때 한 번만 보입니다. 앱의 '키 발급 안내' 버튼에도 같은 안내가 있습니다.
2. '인식 엔진' → 실행 위치 **클라우드 · Groq** → 키 입력 → '연결 테스트'
3. 모델은 `whisper-large-v3`(정확도 우선, 추천) 또는 `whisper-large-v3-turbo`

말소리 구간만 Groq 서버로 전송되고, 키는 번역 API 키처럼 암호화해서 저장합니다.
무료 한도(하루 2,000문장 · 분당 20문장)를 넘으면 한도가 풀릴 때까지 인식을 잠시 멈추고 상태 표시줄에 알려 줍니다.
Groq는 한국어 억양 영어를 'Korean'으로 표시하는 경우가 있어서, 한국어 건너뛰기는 받아 적은 글자(한글 비율)로 판단합니다.

### 음성 채팅만 번역 (VB-CABLE)

게임 소리 전체 대신 음성 채팅만 번역하는 모드입니다. 게임에 음성 채팅 출력 장치를 따로 고르는 설정이 있어야 합니다.

1. '번역할 프로그램' 맨 위의 **음성 채팅만 번역 (VB-CABLE)** 선택
2. VB-CABLE이 없으면 'VB-CABLE 설치' → VB-Audio 설치 창에서 Install Driver → 재부팅
   (VB-CABLE은 VB-Audio의 도네이션웨어입니다. 출처: www.vb-cable.com)
3. 게임의 오디오 설정: **음성 채팅 출력 장치 = CABLE Input (VB-Audio Virtual Cable)**
4. '음성 채팅 듣기'를 켜 두면 앱이 음성 채팅을 헤드셋으로 들려줍니다. 앱을 쓰지 않을 때는 게임 설정을 원래대로(시스템 기본값) 되돌리세요.

VB-CABLE 패키지는 앱에 포함하지 않습니다(패키지 라이선스상 다른 설치 과정에 통합하려면 작성자 동의 필요).
설치 버튼은 공식 사이트에서 받아 VB-Audio의 설치 프로그램을 여는 역할만 합니다.

설정·로그·번역 기록은 `%APPDATA%\EN2KR4GAMING`에 저장됩니다. v1의 `settings_en2kr_whisper_context.json`이
exe 옆에 있으면 처음 실행할 때 자동으로 가져옵니다.

## 개발

```powershell
py -3.10 -m venv .venv            # Python 3.10+
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python EN2KR4GAMING.py
.venv\Scripts\python -m pytest tests
```

- 게임 없이 전체 파이프라인 확인: `python EN2KR4GAMING.py --selftest tests\assets\speech_en.wav [--model distil-large-v3.5] [--mode english|fixed|auto] [--out report.txt]`
- 모델 VRAM·속도 재측정(인식 엔진 창에 표시되는 값): `python tools\measure_models.py tests\assets\speech_en.wav`

### 빌드

```powershell
.venv\Scripts\pyinstaller EN2KR4GAMING.spec --noconfirm
```

결과물: `dist\EN2KR4GAMING\` 폴더 전체를 배포합니다 (exe 단독 실행 불가).

### 구조

```
EN2KR4GAMING.py         실행기
en2kr/
  app.py                진입점 (Qt, 단일 실행, --selftest)
  settings.py           설정 (BOM 허용, 원자적 저장, DPAPI 키 암호화, v1 이전)
  audio/                프로세스 루프백 캡처, 오디오 세션 목록, VB-CABLE 음성 채팅 캡처·모니터링, 장치 캡처(고급)
  vad.py                Silero VAD(ONNX) + 샘플 기준 발화 분할
  stt.py                faster-whisper 엔진·Groq 클라우드 엔진·모델 카탈로그·언어 판별
  translate/            번역 API 어댑터, 용어집/슬랭, 비동기 번역 서비스
  filters.py            환청 필터
  pipeline.py           오디오 → VAD → STT → 번역 스레드 연결
  ui/                   메인 창, 오버레이, 설정 창
tests/                  pytest (음성 샘플 포함)
tools/measure_models.py 모델 VRAM·속도 측정
```

## 문제 해결

- **소리가 안 잡힘**: 상단 '오디오' 상태 확인. 고른 프로그램이 소리를 내기 시작하면 자동으로 연결됩니다.
- **F12가 안 먹음**: 게임이 관리자 권한으로 실행 중이면 이 프로그램도 관리자 권한으로 실행하세요. 단축키는 '오버레이·단축키·기록'에서 바꿀 수 있습니다.
- **로그**: `%APPDATA%\EN2KR4GAMING\logs\app.log`

문의: huneejung@naver.com
