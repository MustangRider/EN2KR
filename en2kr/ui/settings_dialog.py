"""Hotkey and transcript settings (the overlay's look has its own window: overlay_dialog.py)."""
from PySide6 import QtWidgets

from .. import sounds
from ..settings import DEFAULT_EDIT_KEY, DEFAULT_TOGGLE_KEY


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("단축키 · 기록 설정")
        self.settings = settings
        lg = settings.section("logging")
        layout = QtWidgets.QVBoxLayout(self)

        g2 = QtWidgets.QGroupBox("단축키")
        f2 = QtWidgets.QFormLayout(g2)
        self.hotkey = QtWidgets.QLineEdit(settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY))
        self.hotkey.setPlaceholderText(f"예: {DEFAULT_EDIT_KEY}, scroll lock, pause")
        f2.addRow("오버레이 편집 모드:", self.hotkey)
        self.toggle_key = QtWidgets.QLineEdit(settings.get("hotkeys.translate_toggle", DEFAULT_TOGGLE_KEY))
        self.toggle_key.setPlaceholderText(f"예: {DEFAULT_TOGGLE_KEY}, ctrl+shift, f9 (비우면 사용 안 함)")
        f2.addRow("번역 켜기/끄기:", self.toggle_key)
        snd = settings.section("sound")
        self.sound_on = QtWidgets.QCheckBox("켜고 끌 때 소리")
        self.sound_on.setChecked(snd.get("toggle", True))
        self.volume = QtWidgets.QSpinBox()
        self.volume.setRange(5, 100)
        self.volume.setSuffix(" %")
        self.volume.setValue(int(snd.get("volume", 50)))
        sound_row = QtWidgets.QHBoxLayout()
        sound_row.addWidget(self.sound_on)
        sound_row.addWidget(QtWidgets.QLabel("크기"))
        sound_row.addWidget(self.volume)
        for text, on in (("켜짐 듣기", True), ("꺼짐 듣기", False)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, o=on: sounds.play_toggle(o, self.volume.value()))
            sound_row.addWidget(b)
        sound_row.addStretch()
        f2.addRow(sound_row)
        tip = QtWidgets.QLabel("Ctrl·Alt·Shift만으로 된 키(기본 Ctrl+Alt)는 함께 눌렀다가 바로 뗄 때만 동작합니다. 다른 키와 같이 "
                               "누르거나(Ctrl+Alt+O 등), 웅크리기처럼 Ctrl을 누르고 있다가 Alt를 누르거나, 오래 누르면 무시합니다. "
                               "게임에서 쓰지 않는 키를 고르세요 (F12는 스팀 스크린샷 키와 겹칩니다). "
                               "게임이 관리자 권한으로 실행 중이면 이 프로그램도 관리자 권한으로 실행해야 단축키가 동작합니다.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        f2.addRow(tip)
        layout.addWidget(g2)

        g3 = QtWidgets.QGroupBox("번역 기록")
        f3 = QtWidgets.QFormLayout(g3)
        self.save_tx = QtWidgets.QCheckBox("번역 내용을 날짜별 파일로 저장")
        self.save_tx.setChecked(lg.get("save_transcripts", True))
        self.retention = QtWidgets.QSpinBox()
        self.retention.setRange(0, 365)
        self.retention.setValue(int(lg.get("retention_days", 14)))
        self.retention.setSuffix(" 일")
        self.retention.setSpecialValueText("영구 보관")
        f3.addRow(self.save_tx)
        f3.addRow("보관 기간:", self.retention)
        note = QtWidgets.QLabel("기록에는 다른 플레이어의 음성 내용이 들어 있습니다. 필요한 만큼만 보관하세요.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        f3.addRow(note)
        layout.addWidget(g3)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save
                                          | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("저장")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.resize(560, self.sizeHint().height())

    def accept(self):
        edit = self.hotkey.text().strip().lower() or DEFAULT_EDIT_KEY
        if edit == self.toggle_key.text().strip().lower():
            QtWidgets.QMessageBox.warning(self, "단축키", "편집 모드와 번역 켜기/끄기에 같은 키를 쓸 수 없습니다.")
            return
        super().accept()

    def apply(self):
        s = self.settings
        s.set("hotkeys.edit_mode", self.hotkey.text().strip().lower() or DEFAULT_EDIT_KEY)
        s.set("hotkeys.translate_toggle", self.toggle_key.text().strip().lower())
        s.set("sound.toggle", self.sound_on.isChecked())
        s.set("sound.volume", self.volume.value())
        s.set("logging.save_transcripts", self.save_tx.isChecked())
        s.set("logging.retention_days", self.retention.value())
        s.save()
