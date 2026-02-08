# EN2KR4GAMING

실시간 게임 음성 번역 오버레이 (Whisper + DeepL/Azure)

## 실행 전 준비
1) Python 설치
2) 의존성 설치

3) 설정 파일 생성
- settings_en2kr_whisper_context.sample.json 을 복사해
  settings_en2kr_whisper_context.json 으로 저장 후 API 키 입력

## 실행
python EN2KR4GAMING.py

## 모델 파일
- models/silero_vad/silero_vad.jit 필요
- 네트워크에서 자동 다운로드를 허용하려면 실행 전에 환경변수 설정:
  - EN2KR_ALLOW_VAD_DOWNLOAD=1

## 제외된 항목
- 로그, 캐시, 빌드 결과물, 가상환경, 개인 설정 파일은 Git에 포함되지 않습니다.

## 자동 업로드 (저장 시 자동 커밋/푸시)
1) PowerShell에서 아래 스크립트를 실행
  - auto_push.ps1
2) 파일 저장 시 자동으로 commit/push 됩니다.
3) 중지하려면 PowerShell 창을 닫으면 됩니다.
