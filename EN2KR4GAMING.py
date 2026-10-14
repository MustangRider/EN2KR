# ===============================================================
# EN2KR4GAMING - Real-time Game Voice Translation Overlay
#
# Contact: huneejung@naver.com
# Description: 게임 음성(프로그램별 캡처) -> Whisper -> 번역 API -> 오버레이
#
# The application lives in the en2kr package; this file is the launcher
# used by `python EN2KR4GAMING.py` and by the PyInstaller spec.
# ===============================================================
import sys

from en2kr.app import main

if __name__ == "__main__":
    sys.exit(main())
