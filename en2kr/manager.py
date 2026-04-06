"""
EN2KR4GAMING - Manager module
MainWindow class: main control panel UI and data management.
"""

import os
import keyboard
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
        self.setWindowTitle(f"EN2KR4GAMING - Manager [{self.config.selected_api}]")
        self.resize(1100, 850)
        self.setStyleSheet("background-color: #121212; color: #FFFFFF;")
        self.tracker = HallucinationTracker(config, self)
        self.overlay = None  # OverlayWindow 참조 저장용

        central = QtWidgets.QWidget(); self.setCentralWidget(central); layout = QtWidgets.QHBoxLayout(central)
        left_layout = QtWidgets.QVBoxLayout()
        status_bar = QtWidgets.QHBoxLayout()
        self.vad_label = QtWidgets.QLabel("🎤 STANDBY")
        self.vad_label.setStyleSheet("color: #555; font-weight: bold; padding: 5px 15px; background: #222; border-radius: 5px;")
        status_bar.addWidget(QtWidgets.QLabel(f"DEVICE: {self.config.device.upper()} | API: {self.config.selected_api}"))
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
        self.vad_slider.setRange(1, 100); self.vad_slider.setValue(int(self.config.vad_threshold * 100))
        self.vad_slider.valueChanged.connect(self.update_params)
        s_layout.addWidget(self.vad_slider, 0, 1)
        self.vad_val = QtWidgets.QLabel(f"{self.config.vad_threshold:.2f}"); s_layout.addWidget(self.vad_val, 0, 2)

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
        self.sil_slider.setRange(2, 30); self.sil_slider.setValue(int(self.config.silence_duration * 10))
        self.sil_slider.valueChanged.connect(self.update_params)
        s_layout.addWidget(self.sil_slider, 1, 1)
        self.sil_val = QtWidgets.QLabel(f"{self.config.silence_duration:.1f}s"); s_layout.addWidget(self.sil_val, 1, 2)

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

    # ================= 설정 저장 헬퍼 =================

    def _save(self, overlay_geo=None, font_size=None, line_spacing=None, max_display_lines=None):
        save_settings(self.config, SETTINGS_FILE,
                      overlay_geo=overlay_geo, font_size=font_size,
                      line_spacing=line_spacing, max_display_lines=max_display_lines)

    # ================= 슬라이더 / UI 업데이트 =================

    def update_params(self):
        self.config.vad_threshold = self.vad_slider.value() / 100.0
        self.config.silence_duration = self.sil_slider.value() / 10.0
        self.vad_val.setText(f"{self.config.vad_threshold:.2f}")
        self.sil_val.setText(f"{self.config.silence_duration:.1f}s")
        self._save()

    @QtCore.Slot(bool)
    def update_vad_ui(self, active):
        if active:
            self.vad_label.setStyleSheet("color: #FFF; background: #FF0000; font-weight: bold; padding: 5px 15px; border-radius: 5px;")
        else:
            self.vad_label.setStyleSheet("color: #555; background: #222; font-weight: bold; padding: 5px 15px; border-radius: 5px;")

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
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] [번역] {msg}\n")
            except Exception:
                pass

        self.log_display.moveCursor(QtGui.QTextCursor.End)

    # ================= 목록 관리 =================

    def refresh_lists(self):
        with self.config.filter_lock:
            self.hall_list.clear()
            self.hall_list.addItems(sorted(self.config.hallucination_filter))
        with self.config.glossary_lock:
            self.gloss_list.clear()
            for e, k in sorted(self.config.glossary.items()):
                self.gloss_list.addItem(f"{e} -> {k}")

    def add_hall(self):
        t = self.hall_in.text().strip()
        if t:
            with self.config.filter_lock:
                self.config.hallucination_filter.append(t)
            self._save(); self.refresh_lists(); self.hall_in.clear()

    def remove_hall(self):
        i = self.hall_list.currentItem()
        if i:
            with self.config.filter_lock:
                self.config.hallucination_filter.remove(i.text())
            self._save(); self.refresh_lists()

    def add_gloss(self):
        e, k = self.en_in.text().strip(), self.kr_in.text().strip()
        if e and k:
            # 영어 단어를 Title Case로 변환 (각 단어의 첫 글자 대문자)
            e_normalized = e.title()
            with self.config.glossary_lock:
                self.config.glossary[e_normalized] = k
            self._save(); self.refresh_lists(); self.en_in.clear(); self.kr_in.clear()

    def remove_gloss(self):
        i = self.gloss_list.currentItem()
        if i:
            k = i.text().split(" -> ")[0]
            with self.config.glossary_lock:
                if k in self.config.glossary:
                    del self.config.glossary[k]
            self._save(); self.refresh_lists()

    # ================= 설정 도우미 =================

    def open_setup_helper(self):
        """설정 도우미 다이얼로그 열기"""
        wizard = SetupWizardDialog(self, is_initial_setup=False)
        wizard.exec()

    # ================= 도움말 팝업 =================

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

    # ================= 종료 이벤트 =================

    def closeEvent(self, event):
        self.config.running.clear()  # was: running_flag = False
        try:
            if self.overlay:
                self._save(self.overlay.geometry(), self.overlay.font_size,
                           self.overlay.line_spacing, self.overlay.max_display_lines)
            else:
                self._save()
        except Exception as e:
            print(f"[ERROR] 종료 시 설정 저장 실패: {e}")
        keyboard.unhook_all()
        event.accept()
