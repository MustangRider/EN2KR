# ===============================================================
# EN2KR4GAMING - Real-time Game Voice Translation Overlay
# 
# Contact: huneejung@naver.com
# Created: 2026-01-23
# Version: 1.0.0
# 
# Description: Whisper + DeepL/Azure 기반 실시간 음성 번역 툴
# ===============================================================

import sys, os

# ⚠️ 중요: torch import 이전에 CUDA 경로 추가 (EXE 환경)
if getattr(sys, 'frozen', False):
    cuda_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.exists(cuda_base):
        cuda_versions = [d for d in os.listdir(cuda_base) if os.path.isdir(os.path.join(cuda_base, d))]
        cuda_versions.sort(reverse=True)
        for version in cuda_versions:
            cuda_path = os.path.join(cuda_base, version, "bin")
            if os.path.exists(cuda_path):
                os.environ['PATH'] = cuda_path + os.pathsep + os.environ.get('PATH', '')
                break

import queue, threading, keyboard, json, ctypes, re, time, requests, deepl, base64, subprocess, webbrowser
import numpy as np
import sounddevice as sd
import torch
from PySide6 import QtWidgets, QtCore, QtGui
from datetime import datetime, timedelta
from faster_whisper import WhisperModel

# ================= 1. 전역 설정 및 경로 =================
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# VAD 모델 경로 (프로그램 폴더 내부)
VAD_MODEL_DIR = os.path.join(BASE_DIR, "models", "silero_vad")
os.makedirs(VAD_MODEL_DIR, exist_ok=True)

# 한글 경로 에러 방지를 위한 고정 경로 설정 (폴백용)
VAD_CACHE_DIR = 'C:/temp/en2kr_cache'
os.environ['TORCH_HOME'] = VAD_CACHE_DIR
os.makedirs(VAD_CACHE_DIR, exist_ok=True)

LOG_DIR = os.path.join(BASE_DIR, "logs-en2kr")
os.makedirs(LOG_DIR, exist_ok=True) 

try: import win32gui, win32con
except ImportError: win32gui = None

SETTINGS_FILE = os.path.join(BASE_DIR, "settings_en2kr_whisper_context.json")
WHISPER_MODEL_SIZE = "distil-small.en"
TARGET_KEYWORD = "CABLE Output"
SAMPLE_RATE = 16000
VAD_WINDOW_SIZE = 512
MAX_DISPLAY_LINES = 5
MAX_HISTORY_LIMIT = 100
ALLOW_VAD_DOWNLOAD = os.environ.get("EN2KR_ALLOW_VAD_DOWNLOAD", "0") == "1"

def find_optimal_audio_device():
    """
    최적화된 VB 케이블 오디오 장치 선택 (안정성 + 대역폭 우선)
    우선순위:
    1. CABLE Output + DirectSound (16ch) - 안정성 최고 + 전체 대역폭
    2. CABLE Output + MME (16ch) - 호환성 높음
    3. CABLE Output + WASAPI - 품질 우수 (채널 제한 있을 수 있음)
    4. 기타 VB-Audio 입력 장치
    """
    devices = sd.query_devices()
    apis = sd.query_hostapis()
    
    if isinstance(devices, dict):
        return None
    
    def get_device_priority(device_name, channels, hostapi_idx):
        """
        VB 케이블 장치의 우선순위 계산 (hostapi 기반)
        """
        name_lower = device_name.lower()
        is_cable_output = "cable output" in name_lower
        api_name = apis[hostapi_idx]['name'].lower()
        
        # 1순위: DirectSound + 16ch (최고 안정성)
        if is_cable_output and "directsound" in api_name and channels == 16:
            return 0
        
        # 2순위: MME + 16ch (호환성 높음)
        if is_cable_output and "mme" in api_name and channels == 16:
            return 5
        
        # 3순위: WASAPI (품질 우수)
        if is_cable_output and "wasapi" in api_name:
            return 10 + (16 - min(channels, 16))
        
        # 4순위: DirectSound (16ch 미만)
        if is_cable_output and "directsound" in api_name and channels < 16:
            return 20
        
        # 5순위: MME (16ch 미만)
        if is_cable_output and "mme" in api_name and channels < 16:
            return 25
        
        # 6순위: WDM-KS (16ch)
        if is_cable_output and "wdm" in api_name and channels >= 16:
            return 30
        
        # 기타
        return 100
    
    # 입력 채널이 있는 모든 장치 후보 수집
    candidates = []
    for i in range(len(devices)):
        if devices[i]['max_input_channels'] > 0:
            priority = get_device_priority(devices[i]['name'], devices[i]['max_input_channels'], devices[i]['hostapi'])
            candidates.append((i, priority, devices[i]['name'], devices[i]['max_input_channels'], devices[i]['hostapi']))
    
    # 우선순위별로 정렬
    candidates.sort(key=lambda x: x[1])
    
    if candidates:
        selected, priority, device_name, channels, hostapi_idx = candidates[0]
        api = apis[hostapi_idx]['name']
        cable_type = "Virtual Cable" if "virtual cable" in device_name.lower() else ("Point" if "point" in device_name.lower() else "Cable")
        print(f"[AUDIO] 최적 장치 선택: 장치#{selected} - {device_name} ({channels}ch, {cable_type}, {api})")
        return selected
    
    print("[ERROR] 입력 가능한 오디오 장치를 찾을 수 없습니다.")
    return None

running_flag = True
audio_queue = queue.Queue(maxsize=3000)
inference_queue = queue.Queue(maxsize=100)
translation_queue = queue.Queue(maxsize=100)
filter_lock = threading.Lock()
glossary_lock = threading.Lock()

VAD_THRESHOLD = 0.15
SILENCE_DURATION = 0.4
CONTEXT_TIMEOUT = 0.5
CONTEXT_RESET_SECONDS = 3.0
MIN_AVG_LOGPROB = -1.0
MAX_NO_SPEECH_PROB = 0.6

hallucination_filter = ["Okay.", "Yeah.", "But.", "Thank you.", "Thanks.", ".", "thank you for watching", "subscribe", "watching", "subtitle", "...", "So.", "Oh.", "I don't know.", "That's wrong.", "Oh my God, I don't know."]
glossary = {
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
    "blueprint": "설계도"
}

SELECTED_API = ""
API_KEY = ""
AZURE_REGION = "koreacentral"

def encode_key(key): return base64.b64encode(key.encode()).decode()
def decode_key(enc_key): 
    try: return base64.b64decode(enc_key.encode()).decode()
    except: return enc_key

# ================= 2. 설정 관련 다이얼로그 =================

class SetupDialog(QtWidgets.QDialog):
    def __init__(self, initial_api="DeepL", initial_key="", initial_region="koreacentral"):
        super().__init__()
        self.setWindowTitle("Translation API Setup")
        self.setFixedSize(400, 280)
        self.setStyleSheet("background-color: #1e1e1e; color: white; font-family: 'Malgun Gothic';")
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("### 번역 API 선택 및 검증"))
        
        self.api_combo = QtWidgets.QComboBox()
        self.api_combo.addItems(["DeepL", "Azure"])
        self.api_combo.setCurrentText(initial_api)
        self.api_combo.setStyleSheet("background: #333; padding: 5px;")
        layout.addWidget(self.api_combo)
        
        layout.addWidget(QtWidgets.QLabel("API 인증 키 (Auth Key):"))
        self.key_input = QtWidgets.QLineEdit()
        self.key_input.setText(initial_key)
        self.key_input.setPlaceholderText("API 키를 입력하세요...")
        self.key_input.setStyleSheet("background: #333; border: 1px solid #555; padding: 5px;")
        layout.addWidget(self.key_input)
        
        self.region_label = QtWidgets.QLabel("Azure 지역 (Azure 선택 시만):")
        layout.addWidget(self.region_label)
        self.region_input = QtWidgets.QLineEdit()
        self.region_input.setText(initial_region)
        self.region_input.setStyleSheet("background: #333; border: 1px solid #555; padding: 5px;")
        layout.addWidget(self.region_input)
        
        self.status_label = QtWidgets.QLabel("준비됨")
        self.status_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(self.status_label)

        self.api_combo.currentTextChanged.connect(self.toggle_azure_fields)
        self.toggle_azure_fields(self.api_combo.currentText())
        
        btn = QtWidgets.QPushButton("API 검증 및 시작")
        btn.setStyleSheet("background: #0078d4; font-weight: bold; padding: 10px; margin-top: 10px;")
        btn.clicked.connect(self.validate_and_start)
        layout.addWidget(btn)

    def toggle_azure_fields(self, text):
        visible = (text == "Azure")
        self.region_label.setVisible(visible)
        self.region_input.setVisible(visible)

    def validate_and_start(self):
        api = self.api_combo.currentText()
        key = self.key_input.text().strip()
        reg = self.region_input.text().strip()
        
        if not key:
            QtWidgets.QMessageBox.warning(self, "알림", "API 키를 입력해야 합니다.")
            return

        self.status_label.setText("연결 테스트 중...")
        self.status_label.setStyleSheet("color: #ffaa00;")
        QtWidgets.QApplication.processEvents()

        success = False
        try:
            if api == "DeepL":
                t = deepl.Translator(key)
                # 실제 요청 전에 연결 테스트
                result = t.translate_text("Hello", target_lang="KO")
                if not result or not hasattr(result, 'text'):
                    raise RuntimeError("DeepL 번역 응답이 올바르지 않습니다.")
            else:
                url = "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ko"
                headers = {'Ocp-Apim-Subscription-Key': key, 'Ocp-Apim-Subscription-Region': reg, 'Content-type': 'application/json'}
                resp = requests.post(url, headers=headers, json=[{'text': 'Hello'}], timeout=3)
                resp.raise_for_status()
                if not resp.json():
                    raise RuntimeError("Azure 번역 응답이 올바르지 않습니다.")
            success = True
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "검증 실패", f"API 연결에 실패했습니다.\n설정을 확인해주세요.\n\n오류: {str(e)}")
            self.status_label.setText("검증 실패")
            self.status_label.setStyleSheet("color: #ff4444;")

        if success:
            global SELECTED_API, API_KEY, AZURE_REGION
            SELECTED_API, API_KEY, AZURE_REGION = api, key, reg
            # 설정을 파일에 저장
            try:
                settings_data = {}
                if os.path.exists(SETTINGS_FILE):
                    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                        settings_data = json.load(f)
                settings_data.update({
                    "selected_api": SELECTED_API,
                    "api_key": encode_key(API_KEY),
                    "azure_region": AZURE_REGION
                })
                with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(settings_data, f, indent=4, ensure_ascii=False)
                print(f"[SYSTEM] API 설정 저장됨: {SELECTED_API}")
            except Exception as e:
                print(f"[ERROR] 설정 저장 실패: {e}")
            self.accept()

class ReuseConfirmDialog(QtWidgets.QMessageBox):
    def __init__(self, api_type, api_key):
        super().__init__()
        self.setWindowTitle("API 설정 확인")
        masked_key = api_key[:4] + "*" * (len(api_key)-4) if len(api_key) > 4 else "****"
        self.setText(f"기존에 사용하던 설정을 불러오시겠습니까?\n\n API 종류: {api_type}\n 인증키: {masked_key}")
        self.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        self.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Yes)
        self.setStyleSheet("background-color: #1e1e1e; color: white;")

class SetupWizardDialog(QtWidgets.QDialog):
    """초기 설정 마법사 - VB Cable 설치 및 설정 가이드"""
    def __init__(self, parent=None, is_initial_setup=True):
        super().__init__(parent)
        self.is_initial_setup = is_initial_setup
        self.setWindowTitle("EN2KR4GAMING 초기 설정 마법사")
        self.setMinimumSize(700, 550)
        self.setStyleSheet("background-color: #1e1e1e; color: white;")
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # 타이틀
        title = QtWidgets.QLabel("🎮 실시간 번역 오버레이 설치 가이드")
        title.setStyleSheet("font-size: 18pt; font-weight: bold; color: #4CAF50; padding: 10px;")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # 제작자 이메일
        email_label = QtWidgets.QLabel("Contact: huneejung@naver.com")
        email_label.setStyleSheet("font-size: 8pt; color: #888; padding: 2px;")
        email_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(email_label)
        
        # 스크롤 영역
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #2a2a2a; }")
        
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setSpacing(15)
        
        # Step 1: VB Cable 설치
        step1 = self._create_step_widget(
            "VB-Audio Virtual Cable 설치",
            "게임 소리를 프로그램으로 전달하기 위해 가상 오디오 케이블이 필요합니다.",
            "📥 VB Cable 다운로드",
            self.open_vb_cable_site
        )
        content_layout.addWidget(step1)
        
        # Step 2: 게임 출력 설정
        step2 = self._create_step_widget(
            "게임 출력 장치 설정",
            "아래 '고급 볼륨 믹서 열기' 버튼을 누르고\n" +
            "번역할 게임이나 프로그램의 출력을 'CABLE Input'으로 바꿔주세요.\n\n" +
            "이제 게임 소리가 가상 케이블로 전달됩니다.",
            "🎚️ 고급 볼륨 믹서 열기",
            self.open_volume_mixer
        )
        content_layout.addWidget(step2)
        
        # Step 3: 이 장치로 듣기 설정
        step3 = self._create_step_widget(
            "'CABLE Output' 듣기 설정 (중요!)",
            "게임 소리를 들으면서 번역하려면 이 설정이 필수입니다:\n" +
            "① 사운드 제어판을 엽니다\n" +
            "② '녹음' 탭으로 이동\n" +
            "③ 'CABLE Output' 우클릭 → '속성'\n" +
            "④ '수신 대기' 탭 → '이 장치로 듣기' 체크\n" +
            "⑤ 재생 장치를 실제 스피커/헤드셋으로 선택\n" +
            "⑥ 확인 버튼 클릭",
            "🔊 사운드 제어판 열기",
            self.open_sound_control
        )
        content_layout.addWidget(step3)
        
        # Step 4: 테스트
        step4_text = QtWidgets.QLabel(
            "설정 테스트\n\n" +
            "✅ 게임을 실행하고 소리가 정상적으로 들리는지 확인\n" +
            "✅ 프로그램 매니저 창에서 VAD 상태가 빨간불로 변하는지 확인\n" +
            "✅ 오버레이 창에 번역이 나타나는지 확인"
        )
        step4_text.setStyleSheet(
            "background: #2d3e2d; padding: 15px; border-radius: 8px; "
            "border-left: 4px solid #4CAF50; line-height: 1.8;"
        )
        step4_text.setWordWrap(True)
        content_layout.addWidget(step4_text)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # 하단 버튼 (최초 실행 시에만 표시)
        if self.is_initial_setup:
            btn_layout = QtWidgets.QHBoxLayout()
            self.complete_btn = QtWidgets.QPushButton("✓ 설정 완료, 프로그램 시작")
            self.complete_btn.setStyleSheet(
                "QPushButton { background: #4CAF50; color: white; padding: 12px; "
                "font-weight: bold; font-size: 12pt; border-radius: 5px; }" +
                "QPushButton:hover { background: #45a049; }"
            )
            self.complete_btn.clicked.connect(self.accept)
            btn_layout.addWidget(self.complete_btn)
            layout.addLayout(btn_layout)
        
    def _create_step_widget(self, title, description, button_text, button_action):
        """단계별 위젯 생성 헬퍼"""
        widget = QtWidgets.QWidget()
        widget.setStyleSheet("background: #2a2a2a; padding: 15px; border-radius: 8px; border-left: 4px solid #2196F3;")
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(10)
        
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #2196F3;")
        layout.addWidget(title_label)
        
        desc_label = QtWidgets.QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #cccccc; line-height: 1.6;")
        layout.addWidget(desc_label)
        
        btn = QtWidgets.QPushButton(button_text)
        btn.setStyleSheet(
            "QPushButton { background: #2196F3; color: white; padding: 8px 16px; "
            "font-weight: bold; border-radius: 4px; }" +
            "QPushButton:hover { background: #1976D2; }"
        )
        btn.clicked.connect(button_action)
        btn.setMaximumWidth(250)
        layout.addWidget(btn)
        
        return widget
    
    def open_vb_cable_site(self):
        """VB Cable 다운로드 사이트 열기"""
        webbrowser.open("https://vb-audio.com/Cable/")
    
    def open_volume_mixer(self):
        """Windows 볼륨 믹서 열기 (Windows 10/11 스타일)"""
        try:
            subprocess.Popen("explorer ms-settings:apps-volume")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "오류", f"볼륨 믹서를 열 수 없습니다: {e}")
    
    def open_sound_control(self):
        """사운드 제어판 열기 (녹음 탭)"""
        try:
            subprocess.Popen("control mmsys.cpl sounds,,1")  # 녹음 탭으로 직접 이동
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "오류", f"사운드 설정을 열 수 없습니다: {e}")

# ================= 3. 기존 기능 클래스 =================

class HallucinationTracker:
    def __init__(self, main_win):
        self.main_win = main_win
        self.history = {}

    def check_and_ban(self, text):
        global hallucination_filter
        clean = text.strip()
        if not clean: return False
        with filter_lock:
            for banned in hallucination_filter:
                if banned.lower() == clean.lower(): return True
        if len(clean.split()) > 3: return False
        now = datetime.now()
        with filter_lock:
            if clean not in self.history: self.history[clean] = []
            self.history[clean].append(now)
            self.history[clean] = [t for t in self.history[clean] if now - t < timedelta(minutes=10)]
            if len(self.history[clean]) >= 5:
                if clean not in hallucination_filter:
                    hallucination_filter.append(clean)
                    self.main_win.update_list_signal.emit()
                    return True
        return False

class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.Signal(str, str, str)
    update_list_signal = QtCore.Signal()
    vad_ui_signal = QtCore.Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EN2KR4GAMING - Manager [{SELECTED_API}]")
        self.resize(1100, 850)
        self.setStyleSheet("background-color: #121212; color: #FFFFFF;")
        self.load_all_settings()
        self.tracker = HallucinationTracker(self)
        self.overlay = None  # OverlayWindow 참조 저장용
        
        central = QtWidgets.QWidget(); self.setCentralWidget(central); layout = QtWidgets.QHBoxLayout(central)
        left_layout = QtWidgets.QVBoxLayout()
        status_bar = QtWidgets.QHBoxLayout()
        self.vad_label = QtWidgets.QLabel("🎤 STANDBY")
        self.vad_label.setStyleSheet("color: #555; font-weight: bold; padding: 5px 15px; background: #222; border-radius: 5px;")
        status_bar.addWidget(QtWidgets.QLabel(f"DEVICE: {DEVICE.upper()} | API: {SELECTED_API}"))
        status_bar.addStretch()
        
        # 편집모드 도움말 버튼
        edit_help_btn = QtWidgets.QPushButton("?")
        edit_help_btn.setFixedSize(20, 20)
        edit_help_btn.setStyleSheet(
            "QPushButton { background: #555; color: white; border-radius: 10px; font-weight: bold; font-size: 10pt; }" +
            "QPushButton:hover { background: #777; }"
        )
        edit_help_btn.clicked.connect(self.show_edit_mode_help)
        status_bar.addWidget(edit_help_btn)
        
        status_bar.addSpacing(1)  # (?) 버튼과 라벨 간격
        
        # F12 편집모드 안내
        edit_guide = QtWidgets.QLabel("F12 : 편집모드 진입")
        edit_guide.setStyleSheet("color: #FFFF00; font-weight: bold; padding: 5px 10px;")
        status_bar.addWidget(edit_guide)
        
        status_bar.addSpacing(20)  # STANDBY와 거리 두기
        status_bar.addWidget(self.vad_label)
        left_layout.addLayout(status_bar)
        self.log_display = QtWidgets.QTextEdit(); self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("background-color: #000; color: #00FF00; font-family: 'Consolas'; font-size: 10pt;")
        left_layout.addWidget(self.log_display)
        
        slide_group = QtWidgets.QGroupBox("엔진 감도 조절")
        s_layout = QtWidgets.QGridLayout(slide_group)
        
        # VAD 민감도 헤더 (?버튼 왼쪽 배치)
        vad_header = QtWidgets.QHBoxLayout()
        vad_help_btn = QtWidgets.QPushButton("?")
        vad_help_btn.setFixedSize(18, 18)
        vad_help_btn.setStyleSheet(
            "QPushButton { background: #555; color: white; border-radius: 9px; font-weight: bold; font-size: 9pt; padding: 0px; }" +
            "QPushButton:hover { background: #777; }"
        )
        vad_help_btn.clicked.connect(self.show_vad_help)
        vad_header.addWidget(vad_help_btn)
        vad_header.addWidget(QtWidgets.QLabel(" VAD 민감도:"))
        vad_header.addStretch()
        s_layout.addLayout(vad_header, 0, 0)
        
        self.vad_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vad_slider.setRange(1, 100); self.vad_slider.setValue(int(VAD_THRESHOLD*100))
        self.vad_slider.valueChanged.connect(self.update_params)
        s_layout.addWidget(self.vad_slider, 0, 1)
        self.vad_val = QtWidgets.QLabel(f"{VAD_THRESHOLD:.2f}"); s_layout.addWidget(self.vad_val, 0, 2)
        
        # 침묵 대기 헤더 (?버튼 왼쪽 배치)
        sil_header = QtWidgets.QHBoxLayout()
        sil_help_btn = QtWidgets.QPushButton("?")
        sil_help_btn.setFixedSize(18, 18)
        sil_help_btn.setStyleSheet(
            "QPushButton { background: #555; color: white; border-radius: 9px; font-weight: bold; font-size: 9pt; padding: 0px; }" +
            "QPushButton:hover { background: #777; }"
        )
        sil_help_btn.clicked.connect(self.show_silence_help)
        sil_header.addWidget(sil_help_btn)
        sil_header.addWidget(QtWidgets.QLabel(" 침묵 대기:"))
        sil_header.addStretch()
        s_layout.addLayout(sil_header, 1, 0)
        
        self.sil_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sil_slider.setRange(2, 30); self.sil_slider.setValue(int(SILENCE_DURATION*10))
        self.sil_slider.valueChanged.connect(self.update_params)
        s_layout.addWidget(self.sil_slider, 1, 1)
        self.sil_val = QtWidgets.QLabel(f"{SILENCE_DURATION:.1f}s"); s_layout.addWidget(self.sil_val, 1, 2)
        
        # 슬라이더 컬럼 비율 조정 (레이블:슬라이더:값 = 1:5:1)
        s_layout.setColumnStretch(0, 1)
        s_layout.setColumnStretch(1, 5)
        s_layout.setColumnStretch(2, 1)
        left_layout.addWidget(slide_group)

        btn_group = QtWidgets.QGroupBox("데이터 관리")
        b_layout = QtWidgets.QGridLayout(btn_group)
        self.hall_in = QtWidgets.QLineEdit(); b_layout.addWidget(QtWidgets.QLabel("환청 추가:"), 0, 0); b_layout.addWidget(self.hall_in, 0, 1)
        self.hall_btn = QtWidgets.QPushButton("차단 등록"); self.hall_btn.clicked.connect(self.add_hall); b_layout.addWidget(self.hall_btn, 0, 2)
        self.en_in = QtWidgets.QLineEdit(); self.kr_in = QtWidgets.QLineEdit()
        b_layout.addWidget(QtWidgets.QLabel("단어 등록:"), 1, 0); b_layout.addWidget(self.en_in, 1, 1); b_layout.addWidget(self.kr_in, 1, 2)
        self.gloss_btn = QtWidgets.QPushButton("단어 저장"); self.gloss_btn.clicked.connect(self.add_gloss); b_layout.addWidget(self.gloss_btn, 1, 3)
        
        # 설정 도우미 버튼 추가
        self.setup_helper_btn = QtWidgets.QPushButton("⚙️ 설정 도우미")
        self.setup_helper_btn.setStyleSheet(
            "QPushButton { background: #2196F3; color: white; padding: 8px; font-weight: bold; border-radius: 4px; }" +
            "QPushButton:hover { background: #1976D2; }"
        )
        self.setup_helper_btn.clicked.connect(self.open_setup_helper)
        b_layout.addWidget(self.setup_helper_btn, 2, 0, 1, 4)
        
        left_layout.addWidget(btn_group); layout.addLayout(left_layout, 2)

        right_layout = QtWidgets.QVBoxLayout()
        
        # 환청 차단 목록 헤더 (?버튼 포함)
        hall_header = QtWidgets.QHBoxLayout()
        hall_label = QtWidgets.QLabel("🚫 환청 차단 목록")
        hall_help_btn = QtWidgets.QPushButton("?")
        hall_help_btn.setFixedSize(20, 20)
        hall_help_btn.setStyleSheet(
            "QPushButton { background: #555; color: white; border-radius: 10px; font-weight: bold; font-size: 10pt; }" +
            "QPushButton:hover { background: #777; }"
        )
        hall_help_btn.clicked.connect(self.show_hallucination_help)
        hall_header.addWidget(hall_label)
        hall_header.addWidget(hall_help_btn)
        hall_header.addStretch()
        right_layout.addLayout(hall_header)
        
        self.hall_list = QtWidgets.QListWidget(); right_layout.addWidget(self.hall_list)
        self.del_hall_btn = QtWidgets.QPushButton("선택한 환청 삭제"); self.del_hall_btn.clicked.connect(self.remove_hall); right_layout.addWidget(self.del_hall_btn)
        
        # 고유명사 단어장 헤더 (?버튼 포함)
        gloss_header = QtWidgets.QHBoxLayout()
        gloss_label = QtWidgets.QLabel("📖 고유명사 단어장")
        gloss_help_btn = QtWidgets.QPushButton("?")
        gloss_help_btn.setFixedSize(20, 20)
        gloss_help_btn.setStyleSheet(
            "QPushButton { background: #555; color: white; border-radius: 10px; font-weight: bold; font-size: 10pt; }" +
            "QPushButton:hover { background: #777; }"
        )
        gloss_help_btn.clicked.connect(self.show_glossary_help)
        gloss_header.addWidget(gloss_label)
        gloss_header.addWidget(gloss_help_btn)
        gloss_header.addStretch()
        right_layout.addLayout(gloss_header)
        
        self.gloss_list = QtWidgets.QListWidget(); right_layout.addWidget(self.gloss_list)
        self.del_gloss_btn = QtWidgets.QPushButton("선택한 단어 삭제"); self.del_gloss_btn.clicked.connect(self.remove_gloss); right_layout.addWidget(self.del_gloss_btn)
        layout.addLayout(right_layout, 1)

        self.log_signal.connect(self.append_log); self.update_list_signal.connect(self.refresh_lists)
        self.vad_ui_signal.connect(self.update_vad_ui); self.refresh_lists()

    def update_params(self):
        global VAD_THRESHOLD, SILENCE_DURATION
        VAD_THRESHOLD = self.vad_slider.value() / 100.0
        SILENCE_DURATION = self.sil_slider.value() / 10.0
        self.vad_val.setText(f"{VAD_THRESHOLD:.2f}"); self.sil_val.setText(f"{SILENCE_DURATION:.1f}s")
        self.save_all_settings()

    @QtCore.Slot(bool)
    def update_vad_ui(self, active):
        if active: self.vad_label.setStyleSheet("color: #FFF; background: #FF0000; font-weight: bold; padding: 5px 15px; border-radius: 5px;")
        else: self.vad_label.setStyleSheet("color: #555; background: #222; font-weight: bold; padding: 5px 15px; border-radius: 5px;")

    @QtCore.Slot(str, str, str)
    def append_log(self, tag, status, msg):
        now = datetime.now(); timestamp = now.strftime('%H:%M:%S')
        
        # [수정] 제어창(매니저) 로그에서는 STT 결과만 보여주고, 번역 결과는 텍스트 파일로만 기록합니다.
        if tag == "STT":
            color = "#00FF00" if status == "PASS" else "#FF4444"
            self.log_display.append(f"<span style='color: #888;'>[{timestamp}]</span> <span style='color: {color}; font-weight: bold;'>[{status}]</span> {msg}")
        
        # TRANS(번역) 로그는 파일에만 기록 (UI 출력 제외)
        if tag == "TRANS" and status == "PASS":
            file_path = os.path.join(LOG_DIR, f"{now.strftime('%Y-%m-%d')}.txt")
            try:
                with open(file_path, "a", encoding="utf-8") as f: f.write(f"[{timestamp}] [번역] {msg}\n")
            except: pass
            
        self.log_display.moveCursor(QtGui.QTextCursor.End)

    def refresh_lists(self):
        with filter_lock: self.hall_list.clear(); self.hall_list.addItems(sorted(hallucination_filter))
        with glossary_lock:
            self.gloss_list.clear()
            for e, k in sorted(glossary.items()): self.gloss_list.addItem(f"{e} -> {k}")

    def add_hall(self):
        t = self.hall_in.text().strip()
        if t: 
            with filter_lock: hallucination_filter.append(t)
            self.save_all_settings(); self.refresh_lists(); self.hall_in.clear()

    def remove_hall(self):
        i = self.hall_list.currentItem()
        if i: 
            with filter_lock: hallucination_filter.remove(i.text())
            self.save_all_settings(); self.refresh_lists()

    def add_gloss(self):
        e, k = self.en_in.text().strip(), self.kr_in.text().strip()
        if e and k:
            # 영어 단어를 Title Case로 변환 (각 단어의 첫 글자 대문자)
            e_normalized = e.title()
            with glossary_lock: glossary[e_normalized] = k
            self.save_all_settings(); self.refresh_lists(); self.en_in.clear(); self.kr_in.clear()
    
    def open_setup_helper(self):
        """설정 도우미 다이얼로그 열기"""
        wizard = SetupWizardDialog(self, is_initial_setup=False)
        wizard.exec()
    
    def show_hallucination_help(self):
        """환청 차단 기능 설명 팝업"""
        QtWidgets.QMessageBox.information(
            self,
            "🚫 환청 차단 목록 도움말",
            "✨ <b>환청 차단 목록</b>은 Whisper 음성인식 모델이 "
            "실제로 음성이 없었는데도 인식하는 단어들을 차단하는 기능입니다.<br><br>"
            "<b>기능:</b><br>"
            "• 동일한 짧은 표현이 10분 내에 5번 이상 반복되면 자동 차단<br>"
            "• 수동으로 단어를 추가하여 영구 차단 가능<br>"
            "• 예시: 'Okay.', 'Yeah.', 'Thank you.' 등<br><br>"
            "<b>사용법:</b><br>"
            "① 환청 추가 칸에 차단할 단어 입력<br>"
            "② '차단 등록' 버튼 클릭<br>"
            "③ 목록에서 선택 후 '삭제' 버튼으로 해제 가능"
        )
    
    def show_vad_help(self):
        """VAD 민감도 설명 팝업"""
        QtWidgets.QMessageBox.information(
            self,
            "🎤 VAD 민감도 도움말",
            "✨ <b>VAD (Voice Activity Detection) 민감도</b>는 음성이 감지되는 "
            "기준을 조정하는 설정입니다.<br><br>"
            "<b>기능:</b><br>"
            "• 낮을수록: 작은 소리도 음성으로 인식 (민감하게 반응)<br>"
            "• 높을수록: 큰 소리만 음성으로 인식 (둔감하게 반응)<br>"
            "• 배경 소음이 많으면 값을 높이고, 조용한 환경이면 낮추세요<br><br>"
            "<b>추천 설정:</b><br>"
            "• 조용한 환경: 0.10 ~ 0.15<br>"
            "• 일반적 환경: 0.15 ~ 0.25 (기본값)<br>"
            "• 시끄러운 환경: 0.25 ~ 0.40"
        )
    
    def show_silence_help(self):
        """침묵 대기 설명 팝업"""
        QtWidgets.QMessageBox.information(
            self,
            "⏱️ 침묵 대기 도움말",
            "✨ <b>침묵 대기</b>는 음성이 멈춘 후 몇 초를 기다린 뒤 "
            "번역을 시작할지 결정하는 설정입니다.<br><br>"
            "<b>기능:</b><br>"
            "• 짧을수록: 빠르게 반응하지만 문장이 자주 끊김<br>"
            "• 길수록: 느리게 반응하지만 완전한 문장을 인식<br>"
            "• 대화 속도에 맞춰 조정하세요<br><br>"
            "<b>추천 설정:</b><br>"
            "• 빠른 대화/게임: 0.3 ~ 0.5초<br>"
            "• 일반적 속도: 0.5 ~ 0.8초 (기본값)<br>"
            "• 느린 대화/설명: 0.8 ~ 1.5초"
        )
    
    def show_edit_mode_help(self):
        """편집모드 조작법 설명 팝업"""
        QtWidgets.QMessageBox.information(
            self,
            "✏️ 편집모드 도움말",
            "✨ <b>편집모드</b>는 오버레이 창의 크기, 위치, 폰트, 표시 설정을 "
            "조정할 수 있는 모드입니다.<br><br>"
            "<b>편집모드 진입/종료:</b><br>"
            "• <b>F12</b> : 편집모드 전환 (노란 테두리 표시)<br><br>"
            "<b>기본 조작:</b><br>"
            "• 마우스 드래그: 오버레이 위치 이동<br>"
            "• 우측 하단 모서리 드래그: 크기 조정<br>"
            "• 마우스 휠: 스크롤 (이전 번역 보기)<br><br>"
            "<b>폰트 크기:</b><br>"
            "• <b>= (+)</b> : 폰트 크기 증가<br>"
            "• <b>-</b> : 폰트 크기 감소<br><br>"
            "<b>최대 표시 줄 수:</b> (1~20줄)<br>"
            "• <b>Ctrl + 휠업</b> : 표시 줄 수 증가<br>"
            "• <b>Ctrl + 휠다운</b> : 표시 줄 수 감소<br><br>"
            "<b>줄 간격:</b> (0~40px)<br>"
            "• <b>Shift + 휠업</b> : 줄 간격 증가<br>"
            "• <b>Shift + 휠다운</b> : 줄 간격 감소"
        )
    
    def show_glossary_help(self):
        """고유명사 단어장 기능 설명 팝업"""
        QtWidgets.QMessageBox.information(
            self,
            "📖 고유명사 단어장 도움말",
            "✨ <b>고유명사 단어장</b>은 게임 내 고유명사(무기명, 캐릭터명, 지명 등)를 "
            "정확하게 번역하기 위한 기능입니다.<br><br>"
            "<b>기능:</b><br>"
            "• 등록된 영어 단어를 DeepL 번역 대신 지정한 한글로 번역<br>"
            "• 대소문자 구분 없이 작동 (raider/RAIDER/Raider 모두 인식)<br>"
            "• 저장 시 자동으로 Title Case로 통일 (Raider)<br>"
            "• 예시: 'Raider' → '레이더', 'Blue Gate' → '블루 게이트'<br><br>"
            "<b>사용법:</b><br>"
            "① 단어 등록 칸에 영어 단어 입력 (왼쪽)<br>"
            "② 한글 번역 입력 (오른쪽)<br>"
            "③ '단어 저장' 버튼 클릭<br>"
            "④ 목록에서 선택 후 '삭제' 버튼으로 해제 가능"
        )

    def remove_gloss(self):
        i = self.gloss_list.currentItem()
        if i:
            k = i.text().split(" -> ")[0]
            with glossary_lock: 
                if k in glossary: del glossary[k]
            self.save_all_settings(); self.refresh_lists()

    def load_all_settings(self):
        global hallucination_filter, glossary, VAD_THRESHOLD, SILENCE_DURATION, SELECTED_API, API_KEY, AZURE_REGION
        # 기본 단어장 백업
        default_glossary = {
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
            "raders": "레이더", "raiders": "레이더",
            "snap blast grenade": "스냅 블래스트", "Defib": "재세동기",
            "goop": "아이템", "goops": "아이템들", "blueprint": "설계도"
        }
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    hallucination_filter = d.get("hallucinations", hallucination_filter)
                    loaded_glossary = d.get("glossary", {})
                    # 설정 파일의 단어장이 비어있으면 기본값 사용
                    glossary = loaded_glossary if loaded_glossary else default_glossary
                    VAD_THRESHOLD = d.get("vad_threshold", 0.15)
                    SILENCE_DURATION = d.get("silence_duration", 0.8)
                    SELECTED_API = d.get("selected_api", "")
                    API_KEY = decode_key(d.get("api_key", ""))
                    AZURE_REGION = d.get("azure_region", "koreacentral")
            except: pass

    def save_all_settings(self, geo=None, fs=None, ls=None, mdl=None):
        d = {
            "hallucinations": hallucination_filter, 
            "glossary": glossary, 
            "vad_threshold": VAD_THRESHOLD, 
            "silence_duration": SILENCE_DURATION,
            "selected_api": SELECTED_API,
            "api_key": encode_key(API_KEY),
            "azure_region": AZURE_REGION
        }
        if geo: d.update({"x": geo.x(), "y": geo.y(), "w": geo.width(), "h": geo.height()})
        if fs: d["font_size"] = fs
        if ls is not None: d["line_spacing"] = ls
        if mdl is not None: d["max_display_lines"] = mdl
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f: json.dump(d, f, indent=4, ensure_ascii=False)

    def closeEvent(self, event):
        global running_flag
        running_flag = False
        # 설정 저장 (오버레이 정보 포함)
        try:
            if self.overlay:
                self.save_all_settings(self.overlay.geometry(), self.overlay.font_size, 
                                     self.overlay.line_spacing, self.overlay.max_display_lines)
            else:
                self.save_all_settings()
            print(f"[SYSTEM] 프로그램 종료 시 설정 저장 완료")
        except Exception as e:
            print(f"[ERROR] 종료 시 설정 저장 실패: {e}")
        keyboard.unhook_all()
        event.accept()

class OverlayWindow(QtWidgets.QWidget):
    recognition_signal = QtCore.Signal(str)
    vad_status_signal = QtCore.Signal(bool)
    update_signal = QtCore.Signal(str, str, bool, str)  # en, kr, is_complete, mode

    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        self.is_edit_mode = False; self.is_speaking = False
        self.text_lines = []; self.font_size = 18
        self.line_spacing = 12  # 줄 간격 (기본값 12px)
        self.max_display_lines = MAX_DISPLAY_LINES  # 최대 표시 줄 수
        self.scroll_offset = 0; self.pending_buffer = ""; self.last_recognition_time = time.time()
        self.dragging = False; self.offset = QtCore.QPoint()
        self.translator_deepl = None
        self.setWindowFlags(QtCore.Qt.WindowType.WindowStaysOnTopHint | QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.Tool)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    s = json.load(f); self.setGeometry(s.get("x", 100), s.get("y", 100), s.get("w", 800), s.get("h", 250))
                    self.font_size = s.get("font_size", 18)
                    self.line_spacing = s.get("line_spacing", 12)
                    self.max_display_lines = s.get("max_display_lines", MAX_DISPLAY_LINES)
            except: self.setGeometry(100, 100, 800, 250)
        else: self.setGeometry(100, 100, 800, 250)
        
        self.font = QtGui.QFont("Arial", self.font_size, QtGui.QFont.Weight.Bold)
        self.grip = QtWidgets.QSizeGrip(self); self.grip.setFixedSize(30, 30); self.grip.hide()
        self.recognition_signal.connect(self.enqueue_recognition); self.vad_status_signal.connect(self.update_vad_status)
        self.update_signal.connect(self.update_ui)  # 번역 결과 업데이트 시그널 연결
        self.flush_timer = QtCore.QTimer(); self.flush_timer.timeout.connect(self.check_context_flush); self.flush_timer.start(500)
        
        threading.Thread(target=self.translation_worker, daemon=True).start()
        self.show(); QtCore.QTimer.singleShot(500, lambda: self.set_click_through(True))

    def wheelEvent(self, event):
        if self.is_edit_mode:
            delta = event.angleDelta().y()
            modifiers = event.modifiers()
            
            # Ctrl + 휠: 최대 표시 줄 수 조절
            if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                if delta > 0:
                    self.change_max_lines(1)  # 휠업: 증가
                else:
                    self.change_max_lines(-1)  # 휠다운: 감소
            # Shift + 휠: 줄 간격 조절
            elif modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
                if delta > 0:
                    self.change_line_spacing(2)  # 휠업: 증가
                else:
                    self.change_line_spacing(-2)  # 휠다운: 감소
            # 일반 휠: 스크롤
            else:
                if delta > 0:
                    if self.scroll_offset < len(self.text_lines) - self.max_display_lines: self.scroll_offset += 1
                else:
                    if self.scroll_offset > 0: self.scroll_offset -= 1
            self.update()

    def resizeEvent(self, event):
        self.grip.move(self.width() - self.grip.width(), self.height() - self.grip.height()); super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self.is_edit_mode and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging = True; self.offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.dragging: self.move(self.mapToGlobal(event.position().toPoint() - self.offset))

    def mouseReleaseEvent(self, event):
        self.dragging = False; self.main_win.save_all_settings(self.geometry(), self.font_size)

    def check_context_flush(self):
        if self.pending_buffer and (time.time() - self.last_recognition_time > CONTEXT_TIMEOUT):
            txt = self.pending_buffer; self.pending_buffer = ""
            translation_queue.put((txt, True, "UPDATE"))

    def set_click_through(self, enabled):
        if not win32gui: return
        hwnd = int(self.winId()); style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if enabled: style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
        else: style &= ~win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)

    def toggle_edit_mode(self):
        self.is_edit_mode = not self.is_edit_mode; self.set_click_through(not self.is_edit_mode)
        if not self.is_edit_mode:
            self.scroll_offset = 0; self.grip.hide()
            self.main_win.save_all_settings(self.geometry(), self.font_size, self.line_spacing, self.max_display_lines)
        else: self.grip.show(); self.grip.raise_()
        self.update()

    def change_font_size(self, delta):
        if self.is_edit_mode:
            self.font_size = max(10, self.font_size + delta); self.font.setPointSize(self.font_size)
            self.update(); self.main_win.save_all_settings(self.geometry(), self.font_size, self.line_spacing, self.max_display_lines)
    
    def change_max_lines(self, delta):
        """Ctrl + -/+ : 최대 표시 줄 수 조절"""
        if self.is_edit_mode:
            self.max_display_lines = max(1, min(20, self.max_display_lines + delta))
            self.update(); self.main_win.save_all_settings(self.geometry(), self.font_size, self.line_spacing, self.max_display_lines)
    
    def change_line_spacing(self, delta):
        """Shift + -/+ : 줄 간격 조절"""
        if self.is_edit_mode:
            self.line_spacing = max(0, min(40, self.line_spacing + delta))
            self.update(); self.main_win.save_all_settings(self.geometry(), self.font_size, self.line_spacing, self.max_display_lines)

    def translation_worker(self):
        self.translator_deepl = None
        last_init_time = 0
        last_api_selected = None

        def init_deepl():
            try:
                if not SELECTED_API:
                    raise RuntimeError("선택된 API가 없습니다.")
                if not API_KEY or not API_KEY.strip():
                    raise ValueError(f"{SELECTED_API} API 키가 설정되지 않았습니다.")
                
                if SELECTED_API == "DeepL":
                    self.translator_deepl = deepl.Translator(API_KEY)
                    # 간단한 테스트로 API 키 유효성 확인
                    test_result = self.translator_deepl.translate_text("test", target_lang="KO")
                    if not test_result or not hasattr(test_result, 'text'):
                        raise RuntimeError("DeepL API 응답이 올바르지 않습니다.")
                    print(f"[TRANS] DeepL Translator 초기화 성공")
                    return True
            except Exception as e:
                self.translator_deepl = None
                error_msg = f"[{SELECTED_API}] 초기화 실패: {str(e)}"
                try:
                    self.main_win.log_signal.emit("TRANS", "FAIL", error_msg)
                except Exception:
                    print(f"[DeepL INIT ERROR] {error_msg}")
                return False
            return False

        while running_flag:
            try:
                en, is_complete, mode = translation_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # API 선택 변경 시 재초기화
            if last_api_selected != SELECTED_API:
                self.translator_deepl = None
                last_api_selected = SELECTED_API

            # 번역 전 영어 원문 처리
            proc_en = en
            
            # 단어장 단어 치환 (영어 → 한글)
            replaced_words = []
            with glossary_lock:
                for eng_word, kor_word in glossary.items():
                    # 대소문자 구분 없이 단어 단위로 치환
                    pattern = r'\b' + re.escape(eng_word) + r'\b'
                    if re.search(pattern, proc_en, flags=re.IGNORECASE):
                        proc_en = re.sub(pattern, f"【{kor_word}】", proc_en, flags=re.IGNORECASE)
                        replaced_words.append(f"{eng_word}→{kor_word}")

            kr = "[...]"
            retry_count = 3
            last_exc = None
            while retry_count > 0:
                try:
                    if SELECTED_API == "Azure":
                        url = "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ko"
                        headers = {
                            'Ocp-Apim-Subscription-Key': API_KEY,
                            'Ocp-Apim-Subscription-Region': AZURE_REGION,
                            'Content-type': 'application/json'
                        }
                        resp = requests.post(url, headers=headers, json=[{'text': proc_en}], timeout=2.5)
                        resp.raise_for_status()
                        kr = resp.json()[0]['translations'][0]['text']
                    elif SELECTED_API == "DeepL":
                        # 매번 번역 시도 전에 Translator가 없으면 초기화
                        if not self.translator_deepl:
                            current_time = time.time()
                            # 초기화 재시도는 2초 간격 (너무 자주 시도하지 않기)
                            if current_time - last_init_time >= 2:
                                init_deepl()
                                last_init_time = current_time
                        
                        if not self.translator_deepl:
                            raise RuntimeError("DeepL Translator가 초기화되지 않았습니다. API 키와 인터넷 연결을 확인하세요.")
                        
                        res = self.translator_deepl.translate_text(proc_en, target_lang="KO")
                        kr = getattr(res, "text", str(res))
                    else:
                        error_msg = f"지원하지 않는 API입니다: '{SELECTED_API}' (유효한 값: 'DeepL' 또는 'Azure')"
                        print(f"[ERROR] {error_msg}, SELECTED_API 타입: {type(SELECTED_API)}")
                        raise RuntimeError(error_msg)
                    break
                except Exception as e:
                    last_exc = e
                    retry_count -= 1
                    if retry_count == 0:
                        kr = f"[API ERR] {e}"
                    else:
                        time.sleep(0.5)

            # 번역 후 단어장 치환 부분을 원래 단어장 번역으로 복원
            with glossary_lock:
                for eng_word, kor_word in glossary.items():
                    # 【한글】 형태를 찾아서 단어장 번역으로 변경
                    kr = kr.replace(f"【{kor_word}】", kor_word)

            try:
                self.main_win.log_signal.emit("TRANS", "PASS", f"{en} -> {kr}")
            except Exception:
                pass

            # 번역 결과를 UI에 업데이트
            self.update_signal.emit(en, kr, is_complete, mode)


    @QtCore.Slot(str, str, bool, str)
    def update_ui(self, en, kr, is_complete, mode):
        if mode == "UPDATE" and self.text_lines: self.text_lines[-1] = [en, kr, is_complete]
        else:
            self.text_lines.append([en, kr, is_complete])
            if len(self.text_lines) > MAX_HISTORY_LIMIT: self.text_lines.pop(0)
        if not self.is_edit_mode: self.scroll_offset = 0
        self.update()

    @QtCore.Slot(str)
    def enqueue_recognition(self, text):
        self.last_recognition_time = time.time(); clean = text.strip()
        is_complete = clean.endswith(('.', '?', '!'))
        full = f"{self.pending_buffer} {clean}" if self.pending_buffer else clean
        mode = "UPDATE" if self.pending_buffer else "NEW"
        translation_queue.put((full, is_complete, mode))
        if is_complete: self.pending_buffer = ""
        else: self.pending_buffer = full

    @QtCore.Slot(bool)
    def update_vad_status(self, status):
        if self.is_speaking != status: self.is_speaking = status; self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        if self.is_edit_mode:
            p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 160))
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 0), 2)); p.drawRect(self.rect().adjusted(1,1,-1,-1))
            
            # 우하단에 편집 정보 표시
            info_font = QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold)
            p.setFont(info_font)
            info_text = f"폰트: {self.font_size}pt | 최대 줄: {self.max_display_lines} | 줄간격: {self.line_spacing}px"
            info_rect = QtCore.QRect(self.width() - 250, self.height() - 30, 240, 25)
            
            # 텍스트 배경
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 120))
            p.drawRect(info_rect)
            
            # 텍스트 그리기
            p.setPen(QtGui.QColor(255, 255, 0))
            p.drawText(info_rect, QtCore.Qt.AlignmentFlag.AlignCenter, info_text)
            
        p.setBrush(QtGui.QColor(255, 0, 0) if self.is_speaking else QtGui.QColor(100, 100, 100))
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawEllipse(self.width() - 30, 15, 12, 12)
        p.setFont(self.font); fm = p.fontMetrics(); y = fm.height() + 10
        if len(self.text_lines) <= self.max_display_lines: display_data = self.text_lines
        else:
            end_idx = len(self.text_lines) - self.scroll_offset
            start_idx = max(0, end_idx - self.max_display_lines); display_data = self.text_lines[start_idx:end_idx]
        for en, kr, is_comp in display_data:
            alpha = 255 if is_comp else 180
            kr_y = y + fm.height() + 2
            
            # 부드러운 그림자 (블러 효과)
            shadow_offsets = [(2, 2), (2, 1), (1, 2), (1, 1)]
            shadow_color = QtGui.QColor(0, 0, 0, int(alpha * 0.6))
            for dx, dy in shadow_offsets:
                p.setPen(shadow_color)
                p.drawText(15 + dx, y + dy, f"● {en}")
                p.drawText(15 + dx, kr_y + dy, f"ㄴ {kr}")
            
            # 메인 텍스트
            p.setPen(QtGui.QColor(255, 255, 0, alpha))
            p.drawText(15, y, f"● {en}")
            p.setPen(QtGui.QColor(255, 255, 255, alpha))
            p.drawText(15, kr_y, f"ㄴ {kr}")
            y += (fm.height() * 2) + self.line_spacing

# ================= 4. 백엔드 워커 =================

def audio_callback(indata, frames, time_info, status):
    try: 
        if audio_queue.qsize() > 1000:
            try: audio_queue.get_nowait()
            except queue.Empty: pass
        audio_queue.put_nowait(indata.copy().flatten())
    except queue.Full: pass

def vad_worker(main_win, overlay):
    vad_acc = np.array([], dtype=np.float32); speech_buf = []; in_speech = False; last_speech = time.time(); preroll = []
    while running_flag:
        try: chunk = audio_queue.get(timeout=0.1)
        except queue.Empty: continue
        preroll.append(chunk)
        if len(preroll) > 22: preroll.pop(0)
        vad_acc = np.concatenate([vad_acc, chunk])
        while len(vad_acc) >= VAD_WINDOW_SIZE:
            current = vad_acc[:VAD_WINDOW_SIZE]; vad_acc = vad_acc[VAD_WINDOW_SIZE:]
            prob = GLOBAL_VAD(torch.from_numpy(current).float().unsqueeze(0), 16000).item()
            if prob > VAD_THRESHOLD:
                last_speech = time.time()
                if not in_speech: in_speech = True; speech_buf = list(preroll); main_win.vad_ui_signal.emit(True); overlay.vad_status_signal.emit(True)
            if in_speech:
                speech_buf.append(current)
                if (time.time() - last_speech) > SILENCE_DURATION:
                    in_speech = False; main_win.vad_ui_signal.emit(False); overlay.vad_status_signal.emit(False)
                    if speech_buf: inference_queue.put(np.concatenate(speech_buf)); speech_buf = []

def stt_worker(main_win, overlay):
    last_end = time.time()
    last_text = ""  # 마지막으로 처리한 텍스트 저장
    
    while running_flag:
        try: audio_data = inference_queue.get(timeout=0.1)
        except queue.Empty: continue
        audio_data = audio_data - np.mean(audio_data); rms = np.sqrt(np.mean(audio_data**2))
        if rms > 1e-6: audio_data *= (0.1 / (rms + 1e-9))
        cond = (time.time() - last_end) < CONTEXT_RESET_SECONDS
        segments, _ = GLOBAL_WHISPER.transcribe(audio_data, language="en", beam_size=5, condition_on_previous_text=cond)
        
        # 모든 segment를 하나로 합쳐서 전송 (중복 방지)
        valid_segments = []
        for s in segments:
            txt = s.text.strip()
            if s.no_speech_prob < MAX_NO_SPEECH_PROB and s.avg_logprob > MIN_AVG_LOGPROB:
                if main_win.tracker.check_and_ban(txt):
                    main_win.log_signal.emit("STT", "BLOCK", f"차단됨: {txt}")
                else:
                    valid_segments.append(txt)
        
        # 유효한 segment들을 하나로 합쳐서 한 번만 emit
        if valid_segments:
            combined_text = " ".join(valid_segments)
            
            # 이전 텍스트와 중복 제거
            if last_text:
                # 마지막 텍스트의 단어들을 가져옴
                last_words = last_text.lower().split()
                current_words = combined_text.split()
                
                # 현재 텍스트의 시작 부분이 이전 텍스트와 겹치는지 확인
                overlap_count = 0
                for i in range(min(5, len(last_words), len(current_words))):  # 최대 5단어까지만 체크
                    if current_words[i].lower() in last_words[-5:]:  # 마지막 5단어 중에 있으면
                        overlap_count = i + 1
                    else:
                        break
                
                # 겹치는 부분이 있으면 제거
                if overlap_count > 0:
                    combined_text = " ".join(current_words[overlap_count:])
                    if not combined_text.strip():  # 중복 제거 후 내용이 없으면 skip
                        continue
            
            overlay.recognition_signal.emit(combined_text)
            last_text = combined_text
            last_end = time.time()
            main_win.log_signal.emit("STT", "PASS", combined_text)

# ================= 5. 실행 로직 =================

if __name__ == "__main__":
    # Windows 콘솔 창 숨기기 (py 실행 시)
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32')
        user32 = ctypes.WinDLL('user32')
        SW_HIDE = 0
        hWnd = kernel32.GetConsoleWindow()
        if hWnd:
            user32.ShowWindow(hWnd, SW_HIDE)
    
    # GUI 모드에서 sys.stderr/stdout이 None인 경우 처리 (--noconsole 빌드)
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    
    app = QtWidgets.QApplication(sys.argv)
    
    # 초기 로딩 대화상자 표시 (API 설정 전)
    init_dialog = QtWidgets.QProgressDialog("프로그램을 초기화하는 중입니다...", None, 0, 0)
    init_dialog.setWindowTitle("EN2KR4GAMING")
    init_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    init_dialog.setCancelButton(None)
    init_dialog.setMinimumDuration(0)
    init_dialog.setRange(0, 0)  # 무한 프로그레스바
    init_dialog.setFixedSize(400, 100)  # 크기 고정으로 렌더링 보장
    init_dialog.show()
    
    # 창이 제대로 렌더링되도록 이벤트 처리 강제
    for _ in range(10):
        app.processEvents()
    time.sleep(0.2)  # 200ms 대기로 사용자가 볼 수 있도록
    
    # API 설정 초기화 루프 - 유효한 설정이 될 때까지 반복
    setup_complete = False
    while not setup_complete:
        saved_api, saved_key, saved_region = "", "", "koreacentral"
        use_saved = False
        
        # 저장된 설정 로드 시도
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    s = json.load(f)
                    saved_api = s.get("selected_api", "")
                    saved_key = decode_key(s.get("api_key", ""))
                    saved_region = s.get("azure_region", "koreacentral")
            except Exception as e:
                print(f"[ERROR] 설정 파일 로드 실패: {e}")

        # 저장된 설정이 있으면 재사용 확인
        if saved_api and saved_key:
            init_dialog.setLabelText("저장된 API 설정을 확인하는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)  # 텍스트 변경이 보이도록 약간 대기
            
            confirm = ReuseConfirmDialog(saved_api, saved_key)
            init_dialog.hide()  # 사용자 입력 대기 시 로딩창 숨김
            result = confirm.exec()
            if result == QtWidgets.QMessageBox.StandardButton.Yes:
                SELECTED_API, API_KEY, AZURE_REGION = saved_api, saved_key, saved_region
                use_saved = True
                print(f"[SYSTEM] 저장된 설정 사용: {SELECTED_API}")
                setup_complete = True
            elif result == QtWidgets.QMessageBox.StandardButton.No:
                # "No"를 선택하면 새로운 설정 입력 - use_saved = False로 진행
                init_dialog.show()  # 로딩창 다시 표시
                init_dialog.setLabelText("새로운 API 설정을 준비하는 중...")
                for _ in range(5):
                    app.processEvents()
                time.sleep(0.1)
            else:
                # 취소 버튼 - 프로그램 종료
                init_dialog.close()
                sys.exit()

        # 새로운 설정이 필요한 경우
        if not use_saved:
            init_dialog.setLabelText("API 설정 창을 여는 중...")
            for _ in range(5):
                app.processEvents()
            time.sleep(0.1)
            
            setup = SetupDialog(saved_api or "DeepL", saved_key, saved_region)
            init_dialog.hide()  # 사용자 입력 대기 시 로딩창 숨김
            if setup.exec() == QtWidgets.QDialog.Accepted:
                # SetupDialog에서 이미 SELECTED_API, API_KEY 등이 설정되었음
                # 설정이 제대로 저장되었는지 확인
                if SELECTED_API and API_KEY and SELECTED_API.strip() and API_KEY.strip():
                    print(f"[SYSTEM] 새 API 설정 완료: {SELECTED_API}")
                    setup_complete = True
                else:
                    print("[ERROR] 설정이 제대로 저장되지 않았습니다. 다시 시도해주세요.")
                    init_dialog.show()  # 로딩창 다시 표시
                    init_dialog.setLabelText("설정 재시도 중...")
                    for _ in range(5):
                        app.processEvents()
                    time.sleep(0.1)
                    QtWidgets.QMessageBox.warning(None, "알림", "설정이 제대로 저장되지 않았습니다. 다시 시도해주세요.")
            else:
                # SetupDialog 취소 - 프로그램 종료
                print("[INFO] 사용자가 설정을 취소했습니다.")
                init_dialog.close()
                sys.exit()
    
    # 초기 로딩 대화상자 닫기 (API 설정 완료)
    init_dialog.close()
    
    # 최종 설정 확인 및 로깅
    print(f"[SYSTEM] 최종 API Configuration: '{SELECTED_API}' (타입: {type(SELECTED_API).__name__})")
    print(f"[SYSTEM] API Key Set: {bool(API_KEY and API_KEY.strip())}")
    print(f"[SYSTEM] Azure Region: {AZURE_REGION}")

    # 모델 로딩 중 팝업 표시 (진행 상태 표시)
    loading_dialog = QtWidgets.QProgressDialog("AI 모델을 로드 중입니다...", None, 0, 3)
    loading_dialog.setWindowTitle("로딩 중")
    loading_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    loading_dialog.setCancelButton(None)
    loading_dialog.setMinimumDuration(0)
    loading_dialog.setValue(0)
    loading_dialog.show()
    app.processEvents()

    print(f"[SYSTEM] Loading Models (Whisper/VAD)... Please wait.")
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    
    # CUDA 미감지 시 사용자에게 설치 안내
    if not torch.cuda.is_available():
        loading_dialog.hide()
        
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("⚠️ GPU 가속 미사용 경고")
        msg_box.setIcon(QtWidgets.QMessageBox.Warning)
        msg_box.setText(
            "<b>⚠️ NVIDIA GPU가 감지되지 않았습니다.</b><br><br>"
            "<span style='color: #ff8800; font-weight: bold;'>CPU 모드로 실행됩니다. 번역 속도가 매우 느려질 수 있습니다.</span><br><br>"
            "<b>GPU 가속을 원하시나요?</b>"
        )
        msg_box.setInformativeText(
            "📊 <b>성능 비교:</b><br>"
            "• GPU 가속: <span style='color: #00ff00; font-weight: bold;'>실시간 번역 (1~2초)</span><br>"
            "• CPU 모드: <span style='color: #ff0000; font-weight: bold;'>느린 번역 (5~10초)</span><br><br>"
            "✅ GPU 가속을 사용하면 <b>3~5배 빠른 번역 속도</b>를 경험할 수 있습니다.<br><br>"
            "필수 요구사항:<br>"
            "• NVIDIA 그래픽카드 (GTX 900 시리즈 이상)<br>"
            "• CUDA Toolkit 설치<br><br>"
            "'예'를 선택하면 CUDA 다운로드 페이지로 이동합니다.<br>"
            "'아니요'를 선택하면 느린 CPU 모드로 계속 진행합니다."
        )
        msg_box.setStandardButtons(
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        msg_box.setDefaultButton(QtWidgets.QMessageBox.No)
        
        # "다시 보지 않기" 체크박스
        dont_show_again = QtWidgets.QCheckBox("다시 보지 않기")
        msg_box.setCheckBox(dont_show_again)
        
        # 설정 파일에서 무시 여부 확인
        show_cuda_warning = True
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                    if settings.get("ignore_cuda_warning", False):
                        show_cuda_warning = False
        except:
            pass
        
        if show_cuda_warning:
            result = msg_box.exec()
            
            # 체크박스 상태 저장
            if dont_show_again.isChecked():
                try:
                    settings = {}
                    if os.path.exists(SETTINGS_FILE):
                        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                            settings = json.load(f)
                    settings["ignore_cuda_warning"] = True
                    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                        json.dump(settings, f, indent=4, ensure_ascii=False)
                except:
                    pass
            
            if result == QtWidgets.QMessageBox.Yes:
                # CUDA 다운로드 페이지 열기
                webbrowser.open("https://developer.nvidia.com/cuda-downloads")
                QtWidgets.QMessageBox.information(
                    None,
                    "CUDA 설치 안내",
                    "CUDA Toolkit 다운로드 페이지가 열렸습니다.\n\n"
                    "설치 완료 후 프로그램을 재시작하면\n"
                    "자동으로 GPU 가속이 활성화됩니다.\n\n"
                    "현재는 CPU 모드로 계속 진행합니다."
                )
        
        loading_dialog.show()
    
    loading_dialog.setLabelText(f"Whisper 모델 로드 중... (Device: {DEVICE.upper()})")
    loading_dialog.setValue(1)
    app.processEvents()
    GLOBAL_WHISPER = WhisperModel(WHISPER_MODEL_SIZE, device=DEVICE, compute_type="float16" if DEVICE=="cuda" else "int8")
    
    loading_dialog.setLabelText("VAD 모델 로드 중...")
    loading_dialog.setValue(2)
    app.processEvents()
    
    GLOBAL_VAD = None
    vad_load_error = None
    
    try:
        # 시도 1: 프로그램 폴더 내 모델 직접 로드 (.jit 파일)
        vad_jit_path = os.path.join(VAD_MODEL_DIR, "silero_vad.jit")
        if os.path.exists(vad_jit_path):
            try:
                GLOBAL_VAD = torch.jit.load(vad_jit_path)
                GLOBAL_VAD.eval()
                print(f"[VAD] 프로그램 내장 모델 로드 성공: {vad_jit_path}")
            except Exception as e:
                print(f"[VAD] 내장 모델 로드 실패: {e}")
                vad_load_error = e
        
        # 시도 2: torch.hub를 통한 로드 (폴백)
        if GLOBAL_VAD is None:
            torch.hub.set_dir(VAD_CACHE_DIR)
            
            # 2-1: 로컬 캐시에서 로드
            try:
                GLOBAL_VAD, _ = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad', 
                    model='silero_vad', 
                    trust_repo=True,
                    force_reload=False,
                    skip_validation=True
                )
                print("[VAD] 로컬 캐시에서 모델 로드 성공")
            except Exception as e1:
                print(f"[VAD] 로컬 캐시 로드 실패: {e1}")
                
                # 2-2: 사용자가 수동 다운로드한 폴더에서 로드
                local_vad_path = os.path.join(VAD_CACHE_DIR, 'snakers4_silero-vad_master')
                if os.path.exists(local_vad_path):
                    try:
                        GLOBAL_VAD, _ = torch.hub.load(
                            repo_or_dir=local_vad_path,
                            model='silero_vad',
                            source='local',
                            trust_repo=True
                        )
                        print(f"[VAD] 로컬 디렉토리에서 모델 로드 성공: {local_vad_path}")
                    except Exception as e2:
                        print(f"[VAD] 로컬 디렉토리 로드 실패: {e2}")
                        vad_load_error = e2
                else:
                    # 2-3: 인터넷을 통한 다운로드 (옵션)
                    if ALLOW_VAD_DOWNLOAD:
                        try:
                            GLOBAL_VAD, _ = torch.hub.load(
                                repo_or_dir='snakers4/silero-vad', 
                                model='silero_vad', 
                                trust_repo=True,
                                force_reload=True
                            )
                            print("[VAD] 인터넷을 통한 모델 다운로드 성공")
                            
                            # 다운로드 성공 시 .jit 파일로 저장하여 다음 실행 시 사용
                            try:
                                torch.jit.save(torch.jit.script(GLOBAL_VAD), vad_jit_path)
                                print(f"[VAD] 모델을 프로그램 폴더에 저장: {vad_jit_path}")
                            except:
                                pass  # 저장 실패해도 실행은 계속
                                
                        except Exception as e3:
                            print(f"[VAD] 인터넷 다운로드 실패: {e3}")
                            vad_load_error = e3
                    else:
                        vad_load_error = RuntimeError("VAD 자동 다운로드가 비활성화되어 있습니다.")
        
        if GLOBAL_VAD is None:
            raise RuntimeError(f"VAD 모델 로드 실패: {vad_load_error}")
            
    except Exception as e:
        error_msg = str(e)
        help_text = (
            "⚠️ VAD 모델을 로드할 수 없습니다.\n\n"
            "해결 방법:\n\n"
            "1️⃣ 모델 파일 직접 다운로드 (권장):\n"
            "   - https://github.com/snakers4/silero-vad/blob/master/files/silero_vad.jit\n"
            "   - 'Download' 버튼으로 silero_vad.jit 파일 다운로드\n"
            f"   - 다운받은 파일을 다음 경로에 복사:\n"
            f"     {VAD_MODEL_DIR}\\silero_vad.jit\n\n"
            "2️⃣ 전체 저장소 다운로드:\n"
            "   - https://github.com/snakers4/silero-vad 접속\n"
            "   - 'Code' -> 'Download ZIP' 클릭\n"
            f"   - 압축 해제 후 폴더를 다음 경로에 복사:\n"
            f"     {VAD_CACHE_DIR}\\snakers4_silero-vad_master\\\n\n"
            "3️⃣ 네트워크 확인:\n"
            "   - 프록시/방화벽/VPN 설정 확인\n\n"
            f"오류 상세: {error_msg}"
        )
        QtWidgets.QMessageBox.critical(None, "VAD 모델 로드 실패", help_text)
        sys.exit(1)
    
    loading_dialog.setValue(3)
    loading_dialog.close()

    mw = MainWindow(); mw.show(); ov = OverlayWindow(mw)
    mw.overlay = ov  # MainWindow에 overlay 참조 연결
    
    # 편집모드 전환: F12
    keyboard.add_hotkey('f12', ov.toggle_edit_mode)
    
    # 폰트 크기: = / - (편집모드에서만 작동)
    keyboard.add_hotkey('=', lambda: ov.change_font_size(2))  # = 키 (+ 표시)
    keyboard.add_hotkey('-', lambda: ov.change_font_size(-2))  # - 키
    
    # 최대 표시 줄 수: Ctrl + 휠 (wheelEvent에서 처리)
    # 줄 간격: Shift + 휠 (wheelEvent에서 처리)
    
    threading.Thread(target=vad_worker, args=(mw, ov), daemon=True).start()
    threading.Thread(target=stt_worker, args=(mw, ov), daemon=True).start()
    
    # 최적화된 오디오 장치 선택
    idx = find_optimal_audio_device()
    if idx is None:
        print("[ERROR] 적합한 오디오 장치를 찾을 수 없습니다.")
        # 초기 설정 마법사 실행
        QtWidgets.QMessageBox.warning(
            None,
            "오디오 장치 미감지",
            "VB-Audio Virtual Cable이 감지되지 않았습니다.\n초기 설정 마법사를 시작합니다."
        )
        wizard = SetupWizardDialog()
        result = wizard.exec()
        if result != QtWidgets.QDialog.Accepted:
            sys.exit(1)
        # 마법사 완료 후 재시도
        idx = find_optimal_audio_device()
        if idx is None:
            QtWidgets.QMessageBox.critical(
                None,
                "설정 실패",
                "여전히 오디오 장치를 찾을 수 없습니다.\nVB Cable 설치 후 프로그램을 재시작해주세요."
            )
            sys.exit(1)
    
    try:
        stream = sd.InputStream(device=idx, channels=1, samplerate=SAMPLE_RATE, callback=audio_callback)
        stream.start()
        exit_code = app.exec()
    except Exception as e:
        print(f"Stream Error: {e}")
        exit_code = 1
    finally:
        running_flag = False
        if 'stream' in locals():
            stream.stop()
            stream.close()
        sys.exit(exit_code)
