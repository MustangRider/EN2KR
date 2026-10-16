"""Customise the overlay: live preview, presets, text / box / layout options, quick placement,
style files. Changes show at once on the real overlay (with sample lines); Cancel puts everything back."""
import json

from PySide6 import QtCore, QtGui, QtWidgets

from ..settings import OVERLAY_STYLE_DEFAULTS
from .subtitle_style import MARKER_MAX, PLACEMENTS, PRESETS, SubtitleRenderer, clean_style, sample_entries

STYLE_FILE_TAG = "en2kr_overlay_style"
EFFECTS = [("shadow", "그림자"), ("outline", "외곽선"), ("none", "없음")]
ALIGNS = [("left", "왼쪽"), ("center", "가운데"), ("right", "오른쪽")]
ANCHORS = [("top", "위쪽부터"), ("bottom", "아래쪽에 붙여서")]
ORDERS = [(False, "아래에 추가 (최신이 맨 아래)"), (True, "위에 추가 (최신이 맨 위)")]


class ColorButton(QtWidgets.QPushButton):
    changed = QtCore.Signal(str)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.value = "#ffffff"
        self.setFixedWidth(96)
        self.clicked.connect(self._pick)

    def set_value(self, value):
        self.value = value
        text = "#000000" if QtGui.QColor(value).lightness() > 140 else "#ffffff"
        self.setText(value.upper())
        self.setStyleSheet(f"QPushButton {{ background: {value}; color: {text}; border: 1px solid #555; }}")

    def _pick(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.value), self, self.title)
        if c.isValid():
            self.set_value(c.name())
            self.changed.emit(c.name())


class Preview(QtWidgets.QWidget):
    """Sample subtitles over a game-like backdrop, drawn by the overlay's own renderer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = SubtitleRenderer()
        self.setMinimumHeight(230)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def set_style(self, cfg):
        self.renderer.set_style(cfg)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = self.rect()
        sky = QtGui.QLinearGradient(0, 0, 0, r.height())
        sky.setColorAt(0, QtGui.QColor("#6d8fa8"))
        sky.setColorAt(0.55, QtGui.QColor("#a9b7a0"))
        sky.setColorAt(0.56, QtGui.QColor("#5f6b4a"))
        sky.setColorAt(1, QtGui.QColor("#39402c"))
        p.fillRect(r, sky)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for x, w, h, col in ((0.08, 0.16, 0.30, "#4a4f55"), (0.30, 0.10, 0.42, "#565b52"),
                             (0.62, 0.22, 0.25, "#3f4549"), (0.86, 0.12, 0.36, "#50574c")):
            p.setBrush(QtGui.QColor(col))
            p.drawRect(QtCore.QRectF(r.width() * x, r.height() * (0.56 - h), r.width() * w, r.height() * h))
        bg = int(self.renderer.cfg.get("bg_opacity", 0) * 2.55)
        if bg:
            p.fillRect(r, QtGui.QColor(0, 0, 0, bg))
        self.renderer.dpr = self.devicePixelRatioF()
        items = [(e, 1.0, 1.0) for e in sample_entries(int(self.renderer.cfg["max_lines"]))]
        self.renderer.paint(p, r, items, speaking=True)
        p.end()


class OverlayStyleDialog(QtWidgets.QDialog):
    def __init__(self, settings, overlay, parent=None):
        super().__init__(parent)
        self.setWindowTitle("오버레이 꾸미기")
        self.settings, self.overlay = settings, overlay
        saved = settings.section("overlay")
        self.cfg = {**OVERLAY_STYLE_DEFAULTS, **clean_style(saved)}
        self._geometry = overlay.geometry()
        self._loading = False
        self._controls = {}   # setting key -> (kind, widget)
        layout = QtWidgets.QVBoxLayout(self)

        self.preview = Preview()
        layout.addWidget(self.preview, 1)
        presets = QtWidgets.QHBoxLayout()
        presets.addWidget(QtWidgets.QLabel("프리셋:"))
        for name in PRESETS:
            b = QtWidgets.QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self._apply(PRESETS[n]))
            presets.addWidget(b)
        presets.addStretch()
        layout.addLayout(presets)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._text_tab(), "글자")
        tabs.addTab(self._layout_tab(), "배경·배치")
        tabs.addTab(self._place_tab(), "화면 위치")
        tabs.addTab(self._extra_tab(), "기타")
        layout.addWidget(tabs)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save
                                          | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("저장")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._load_controls()
        self.resize(780, 760)
        self.overlay.set_style_preview(self.cfg)

    # ---------- controls ----------
    def _spin(self, key, suffix, special=None):
        s = QtWidgets.QSpinBox()
        lo, hi = {"font_size": (8, 72), "line_spacing": (0, 40), "max_lines": (1, 20), "fade_sec": (0, 120),
                  "bg_opacity": (0, 80), "text_opacity": (30, 100), "effect_size": (1, 6),
                  "box_opacity": (0, 100), "original_scale": (50, 150)}[key]
        s.setRange(lo, hi)
        s.setSuffix(suffix)
        if special:
            s.setSpecialValueText(special)
            s.setMinimumWidth(130)
        s.valueChanged.connect(lambda v, k=key: self._set(k, v))
        self._controls[key] = ("spin", s)
        return s

    def _check(self, key, text):
        c = QtWidgets.QCheckBox(text)
        c.toggled.connect(lambda v, k=key: self._set(k, v))
        self._controls[key] = ("check", c)
        return c

    def _combo(self, key, items):
        c = QtWidgets.QComboBox()
        for value, label in items:
            c.addItem(label, value)
        c.currentIndexChanged.connect(lambda _i, k=key, w=c: self._set(k, w.currentData()))
        self._controls[key] = ("combo", c)
        return c

    def _color(self, key, title):
        b = ColorButton(title)
        b.changed.connect(lambda v, k=key: self._set(k, v))
        self._controls[key] = ("color", b)
        return b

    def _marker(self, key):
        e = QtWidgets.QLineEdit()
        e.setMaxLength(MARKER_MAX)
        e.setFixedWidth(70)
        e.setPlaceholderText("없음")
        e.textChanged.connect(lambda v, k=key: self._set(k, v.strip()))
        self._controls[key] = ("text", e)
        return e

    @staticmethod
    def _row(*widgets):
        row = QtWidgets.QHBoxLayout()
        for w in widgets:
            if isinstance(w, str):
                row.addWidget(QtWidgets.QLabel(w))
            else:
                row.addWidget(w)
        row.addStretch()
        return row

    def _text_tab(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self.font_box = QtWidgets.QFontComboBox()
        self.font_box.currentFontChanged.connect(lambda font: self._set("font_family", font.family()))
        f.addRow("글꼴:", self._row(self.font_box, self._spin("font_size", " pt"), self._check("bold", "굵게")))
        f.addRow("원문:", self._row(self._check("show_original", "표시"), "크기", self._spin("original_scale", " %"),
                                   "색", self._color("original_color", "원문 색"), "기호", self._marker("original_marker")))
        f.addRow("번역:", self._row("색", self._color("translation_color", "번역 색"), "기호",
                                   self._marker("translation_marker"), "오류 색", self._color("error_color", "오류 색")))
        f.addRow("글자 불투명도:", self._row(self._spin("text_opacity", " %")))
        f.addRow("테두리 효과:", self._row(self._combo("effect", EFFECTS), "색", self._color("effect_color", "효과 색"),
                                      "두께", self._spin("effect_size", " px")))
        note = QtWidgets.QLabel("외곽선은 밝은 배경에서도 잘 읽힙니다. 기호는 왼쪽 정렬일 때 줄바꿈된 줄이 기호 뒤에 맞춰집니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _layout_tab(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.addRow("줄 배경 상자:", self._row(self._check("box", "켜기"), "색", self._color("box_color", "상자 색"),
                                       "불투명도", self._spin("box_opacity", " %")))
        f.addRow("창 배경 어둡게:", self._row(self._spin("bg_opacity", " %", "투명")))
        f.addRow("정렬:", self._row(self._combo("align", ALIGNS)))
        f.addRow("창 안에서 자막 위치:", self._row(self._combo("anchor", ANCHORS)))
        f.addRow("새 줄:", self._row(self._combo("newest_first", ORDERS)))
        f.addRow("순서:", self._row(self._check("translation_first", "번역을 원문보다 위에")))
        f.addRow("최대 표시 줄 수:", self._row(self._spin("max_lines", " 줄"), "줄 간격", self._spin("line_spacing", " px")))
        f.addRow("자동으로 사라지기:", self._row(self._spin("fade_sec", " 초", "사라지지 않음")))
        return w

    def _place_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.screen_box = QtWidgets.QComboBox()
        current = self.overlay.screen()
        for i, s in enumerate(QtGui.QGuiApplication.screens(), 1):
            g = s.geometry()
            self.screen_box.addItem(f"{i}번 모니터 · {g.width()}×{g.height()}" + (" (지금 위치)" if s is current else ""), i - 1)
            if s is current:
                self.screen_box.setCurrentIndex(i - 1)
        v.addLayout(self._row("모니터:", self.screen_box))
        grid = QtWidgets.QGridLayout()
        spots = {"top": (0, 1), "center": (1, 1), "bottom_left": (2, 0), "bottom": (2, 1), "bottom_right": (2, 2)}
        for key, (r, c) in spots.items():
            b = QtWidgets.QPushButton(PLACEMENTS[key])
            b.clicked.connect(lambda _=False, k=key: self._place(k))
            grid.addWidget(b, r, c)
        v.addLayout(grid)
        note = QtWidgets.QLabel("누르면 오버레이가 바로 옮겨집니다 (취소하면 원래 자리로). 크기는 편집 모드에서 오른쪽 아래 모서리로 조절하세요.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addWidget(self._check("snap", "편집 모드에서 끌 때 화면 가장자리·가운데에 자석처럼 붙이기 (Alt를 누른 채 끌면 안 붙음)"))
        v.addStretch()
        return w

    def _extra_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.addWidget(self._check("show_speaking", "말하는 중 표시 (오른쪽 위 빨간 점)"))
        v.addWidget(self._check("toolbar", "편집 모드에서 도구 막대 보이기 (글자·줄 수·배경·위치·꾸미기)"))
        row = QtWidgets.QHBoxLayout()
        for text, slot in (("스타일 내보내기…", self._export), ("스타일 가져오기…", self._import),
                           ("기본값으로 되돌리기", lambda: self._apply(OVERLAY_STYLE_DEFAULTS))):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        note = QtWidgets.QLabel("스타일 파일에는 글꼴·색·배치 같은 모양만 들어가고, 오버레이 위치와 크기는 들어가지 않습니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch()
        return w

    def _load_controls(self):
        self._loading = True
        try:
            self.font_box.setCurrentFont(QtGui.QFont(self.cfg["font_family"]))
            for key, (kind, widget) in self._controls.items():
                value = self.cfg[key]
                if kind == "spin":
                    widget.setValue(int(value))
                elif kind == "check":
                    widget.setChecked(bool(value))
                elif kind == "combo":
                    widget.setCurrentIndex(max(0, widget.findData(value)))
                elif kind == "color":
                    widget.set_value(value)
                elif kind == "text":
                    widget.setText(value)
        finally:
            self._loading = False
        self._changed()

    # ---------- changes ----------
    def _set(self, key, value):
        if self._loading:
            return
        self.cfg[key] = value
        self._changed()

    def _apply(self, values):
        self.cfg.update(clean_style(values))
        self._load_controls()

    def _changed(self):
        self.preview.set_style(self.cfg)
        self.overlay.set_style_preview(self.cfg)

    def _place(self, where):
        screens = QtGui.QGuiApplication.screens()
        index = self.screen_box.currentData() or 0
        self.overlay.place(where, screens[index] if index < len(screens) else None)

    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "스타일 내보내기", "en2kr_overlay_style.json",
                                                        "스타일 파일 (*.json)")
        if not path:
            return
        data = {STYLE_FILE_TAG: 1, **{k: self.cfg[k] for k in OVERLAY_STYLE_DEFAULTS}}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "스타일 내보내기", f"저장하지 못했습니다: {e}")

    def _import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "스타일 가져오기", "", "스타일 파일 (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "스타일 가져오기", f"읽지 못했습니다: {e}")
            return
        values = clean_style(data)
        if not isinstance(data, dict) or STYLE_FILE_TAG not in data or not values:
            QtWidgets.QMessageBox.warning(self, "스타일 가져오기", "이 프로그램의 스타일 파일이 아닙니다.")
            return
        self._apply(values)

    # ---------- finish ----------
    def accept(self):
        for key in OVERLAY_STYLE_DEFAULTS:
            self.settings.set(f"overlay.{key}", self.cfg[key])
        self.settings.save()
        self.overlay.set_style_preview(None)
        super().accept()

    def reject(self):
        self.overlay.set_style_preview(None)
        if self.overlay.geometry() != self._geometry:
            self.overlay.setGeometry(self._geometry)
            self.overlay._save_geometry()
        super().reject()
