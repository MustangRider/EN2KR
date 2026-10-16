"""Overlay appearance, hotkey and transcript settings."""
from PySide6 import QtGui, QtWidgets

from ..settings import DEFAULT_EDIT_KEY


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("오버레이 · 단축키 · 기록 설정")
        self.settings = settings
        ov = settings.section("overlay")
        lg = settings.section("logging")
        layout = QtWidgets.QVBoxLayout(self)

        g = QtWidgets.QGroupBox("오버레이")
        f = QtWidgets.QFormLayout(g)
        self.font = QtWidgets.QFontComboBox()
        self.font.setCurrentFont(QtGui.QFont(ov.get("font_family") or "Malgun Gothic"))
        self.size = self._spin(8, 72, ov["font_size"], " pt")
        self.lines = self._spin(1, 20, ov["max_lines"], " 줄")
        self.spacing = self._spin(0, 40, ov["line_spacing"], " px")
        self.fade = self._spin(0, 120, ov.get("fade_sec", 0), " 초")
        self.fade.setSpecialValueText("사라지지 않음")
        self.bg = self._spin(0, 80, ov.get("bg_opacity", 0), " %")
        self.bg.setSpecialValueText("투명")
        self.orig = QtWidgets.QCheckBox("원문(외국어)도 함께 표시")
        self.orig.setChecked(ov.get("show_original", True))
        f.addRow("글꼴:", self.font)
        f.addRow("글자 크기:", self.size)
        f.addRow("최대 표시 줄 수:", self.lines)
        f.addRow("줄 간격:", self.spacing)
        f.addRow("자동으로 사라지기:", self.fade)
        f.addRow("배경 어둡게:", self.bg)
        f.addRow(self.orig)
        layout.addWidget(g)

        g2 = QtWidgets.QGroupBox("단축키")
        f2 = QtWidgets.QFormLayout(g2)
        self.hotkey = QtWidgets.QLineEdit(settings.get("hotkeys.edit_mode", DEFAULT_EDIT_KEY))
        self.hotkey.setPlaceholderText(f"예: {DEFAULT_EDIT_KEY}, scroll lock, pause")
        f2.addRow("편집 모드 전환:", self.hotkey)
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
        self.retention = self._spin(0, 365, lg.get("retention_days", 14), " 일")
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

    @staticmethod
    def _spin(lo, hi, value, suffix):
        s = QtWidgets.QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(value))
        s.setSuffix(suffix)
        return s

    def apply(self):
        s = self.settings
        s.set("overlay.font_family", self.font.currentFont().family())
        s.set("overlay.font_size", self.size.value())
        s.set("overlay.max_lines", self.lines.value())
        s.set("overlay.line_spacing", self.spacing.value())
        s.set("overlay.fade_sec", self.fade.value())
        s.set("overlay.bg_opacity", self.bg.value())
        s.set("overlay.show_original", self.orig.isChecked())
        s.set("hotkeys.edit_mode", self.hotkey.text().strip().lower() or DEFAULT_EDIT_KEY)
        s.set("logging.save_transcripts", self.save_tx.isChecked())
        s.set("logging.retention_days", self.retention.value())
        s.save()
