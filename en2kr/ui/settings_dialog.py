"""Hotkey and transcript settings (the overlay's look has its own window: overlay_dialog.py)."""
from PySide6 import QtWidgets

from ..settings import DEFAULT_EDIT_KEY


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
        tip = QtWidgets.QLabel("게임에서 쓰지 않는 키를 고르세요 (F12는 스팀 스크린샷 키와 겹칩니다). "
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
        self.resize(460, self.sizeHint().height())

    def apply(self):
        s = self.settings
        s.set("hotkeys.edit_mode", self.hotkey.text().strip().lower() or DEFAULT_EDIT_KEY)
        s.set("logging.save_transcripts", self.save_tx.isChecked())
        s.set("logging.retention_days", self.retention.value())
        s.save()
