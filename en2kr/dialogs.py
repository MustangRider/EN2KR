"""
EN2KR4GAMING - Dialog classes
SetupDialog, ReuseConfirmDialog, SetupWizardDialog
"""

import subprocess
import webbrowser

import deepl
import requests
from PySide6 import QtWidgets, QtCore

from en2kr.config import AppConfig, save_settings


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle("Translation API Setup")
        self.setFixedSize(400, 280)
        self.setStyleSheet("background-color: #1e1e1e; color: white; font-family: 'Malgun Gothic';")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("### 번역 API 선택 및 검증"))

        self.api_combo = QtWidgets.QComboBox()
        self.api_combo.addItems(["DeepL", "Azure"])
        self.api_combo.setCurrentText(self.config.selected_api)
        self.api_combo.setStyleSheet("background: #333; padding: 5px;")
        layout.addWidget(self.api_combo)

        layout.addWidget(QtWidgets.QLabel("API 인증 키 (Auth Key):"))
        self.key_input = QtWidgets.QLineEdit()
        self.key_input.setText(self.config.api_key)
        self.key_input.setPlaceholderText("API 키를 입력하세요...")
        self.key_input.setStyleSheet("background: #333; border: 1px solid #555; padding: 5px;")
        layout.addWidget(self.key_input)

        self.region_label = QtWidgets.QLabel("Azure 지역 (Azure 선택 시만):")
        layout.addWidget(self.region_label)
        self.region_input = QtWidgets.QLineEdit()
        self.region_input.setText(self.config.azure_region)
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
            self.config.selected_api = api
            self.config.api_key = key
            self.config.azure_region = reg
            save_settings(self.config)
            print(f"[SYSTEM] API 설정 저장됨: {self.config.selected_api}")
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
