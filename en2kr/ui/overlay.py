"""Transparent, click-through subtitle overlay drawn over the game.

Lines are keyed by line_id: the original appears as soon as it is recognised
and the translation fills in when it arrives. Long sentences wrap instead of
being cut off at the window edge (v1 drew single lines).
"""
import ctypes
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
MAX_ENTRIES = 100
FADE_OUT_SEC = 1.0


@dataclass
class Entry:
    en: str
    kr: Optional[str]
    error: Optional[str]
    lang: str
    time: float


class OverlayWindow(QtWidgets.QWidget):
    def __init__(self, settings):
        super().__init__(None, QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("EN2KR4GAMING Overlay")
        self.settings = settings
        self.entries = OrderedDict()
        self.edit_mode = False
        self.speaking = False
        self.scroll = 0
        self._drag = None
        self.grip = QtWidgets.QSizeGrip(self)
        self.grip.setFixedSize(24, 24)
        self.grip.hide()
        self._fade_timer = QtCore.QTimer(self, interval=250, timeout=self.update)
        ov = settings.section("overlay")
        self.setGeometry(ov["x"], ov["y"], ov["w"], ov["h"])
        self._ensure_on_screen()
        self.apply_settings()

    # ---------- settings ----------
    def apply_settings(self):
        ov = self.settings.section("overlay")
        self.cfg = ov
        self.font = QtGui.QFont(ov.get("font_family") or "Malgun Gothic", int(ov["font_size"]), QtGui.QFont.Weight.Bold)
        if ov.get("fade_sec", 0) > 0:
            self._fade_timer.start()
        else:
            self._fade_timer.stop()
        self.update()

    def _set(self, key, value):
        self.settings.set(f"overlay.{key}", value)
        self.cfg[key] = value

    def _save_geometry(self):
        g = self.geometry()
        for k, v in (("x", g.x()), ("y", g.y()), ("w", g.width()), ("h", g.height())):
            self._set(k, v)
        self.settings.save()

    def _ensure_on_screen(self):
        screens = QtGui.QGuiApplication.screens()
        if screens and not any(s.availableGeometry().intersects(self.geometry()) for s in screens):
            self.move(100, 100)

    # ---------- content ----------
    def upsert_source(self, line_id, text, lang):
        e = self.entries.get(line_id)
        if e:
            e.en, e.kr, e.error, e.time = text, None, None, time.monotonic()
        else:
            self.entries[line_id] = Entry(text, None, None, lang, time.monotonic())
            while len(self.entries) > MAX_ENTRIES:
                self.entries.popitem(last=False)
        if not self.edit_mode:
            self.scroll = 0
        self.update()

    def set_translation(self, line_id, source, text, error):
        e = self.entries.get(line_id)
        if e is None:
            e = self.entries[line_id] = Entry(source, None, None, "en", time.monotonic())
        if source and e.en != source:
            return  # a newer merged version of this line is already showing
        e.kr, e.error, e.time = text, error, time.monotonic()
        self.update()

    def set_speaking(self, active):
        if self.speaking != active:
            self.speaking = active
            self.update()

    def clear(self):
        self.entries.clear()
        self.update()

    # ---------- edit mode ----------
    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, lambda: self._set_click_through(not self.edit_mode))

    def _set_click_through(self, enabled):
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_NOACTIVATE
        style = style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        self._set_click_through(not self.edit_mode)
        self.grip.setVisible(self.edit_mode)
        if self.edit_mode:
            self.grip.raise_()
        else:
            self.scroll = 0
            self._save_geometry()
        self.update()
        return self.edit_mode

    def change_font(self, delta):
        if self.edit_mode:
            self._set("font_size", max(8, min(72, self.cfg["font_size"] + delta)))
            self.font.setPointSize(self.cfg["font_size"])
            self.update()

    def change_max_lines(self, delta):
        self._set("max_lines", max(1, min(20, self.cfg["max_lines"] + delta)))
        self.update()

    def change_spacing(self, delta):
        self._set("line_spacing", max(0, min(40, self.cfg["line_spacing"] + delta)))
        self.update()

    # ---------- mouse ----------
    def mousePressEvent(self, event):
        if self.edit_mode and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self._save_geometry()

    def wheelEvent(self, event):
        if not self.edit_mode:
            return
        d = event.angleDelta()
        step = 1 if (d.y() or d.x()) > 0 else -1
        mods = event.modifiers()
        if mods & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.change_max_lines(step)
        elif mods & QtCore.Qt.KeyboardModifier.ShiftModifier:
            self.change_spacing(2 * step)
        else:
            self.scroll = max(0, min(len(self.entries) - 1, self.scroll + step))
            self.update()

    def resizeEvent(self, event):
        self.grip.move(self.width() - self.grip.width(), self.height() - self.grip.height())
        super().resizeEvent(event)

    # ---------- painting ----------
    def _visible_entries(self):
        now = time.monotonic()
        fade = float(self.cfg.get("fade_sec", 0) or 0)
        items = list(self.entries.values())
        end = len(items) - self.scroll
        items = items[max(0, end - int(self.cfg["max_lines"])):end]
        out = []
        for e in items:
            alpha = 1.0
            if fade > 0 and not self.edit_mode:
                age = now - e.time
                if age > fade + FADE_OUT_SEC:
                    continue
                if age > fade:
                    alpha = 1.0 - (age - fade) / FADE_OUT_SEC
            out.append((e, alpha))
        return out

    def _blocks(self, e):
        blocks = []
        if self.cfg.get("show_original", True):
            blocks.append((f"● {e.en}", QtGui.QColor(255, 230, 80)))
        if e.error:
            blocks.append((f"ㄴ ⚠ {e.error[:120]}", QtGui.QColor(255, 150, 80)))
        elif e.kr is None:
            blocks.append(("ㄴ …", QtGui.QColor(170, 170, 170)))
        else:
            blocks.append((f"ㄴ {e.kr}", QtGui.QColor(255, 255, 255)))
        return blocks

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        bg = 160 if self.edit_mode else int(float(self.cfg.get("bg_opacity", 0)) * 2.55)
        if bg:
            p.fillRect(rect, QtGui.QColor(0, 0, 0, bg))
        if self.edit_mode:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 220, 0), 2))
            p.drawRect(rect.adjusted(1, 1, -1, -1))
            info = (f"편집 모드 · 글자 {self.cfg['font_size']}pt(=/-) · 줄 {self.cfg['max_lines']}(Ctrl+휠) · "
                    f"간격 {self.cfg['line_spacing']}px(Shift+휠)")
            p.setFont(QtGui.QFont("Malgun Gothic", 9, QtGui.QFont.Weight.Bold))
            r = QtCore.QRect(8, rect.height() - 26, rect.width() - 40, 20)
            p.setPen(QtGui.QColor(255, 220, 0))
            p.drawText(r, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, info)

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(255, 60, 60) if self.speaking else QtGui.QColor(110, 110, 110, 180))
        p.drawEllipse(rect.width() - 22, 10, 10, 10)

        p.setFont(self.font)
        fm = QtGui.QFontMetrics(self.font)
        width = max(50, rect.width() - 45)
        flags = QtCore.Qt.TextFlag.TextWordWrap | QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        laid = []
        for e, alpha in self._visible_entries():
            blocks = [(t, c, fm.boundingRect(QtCore.QRect(0, 0, width, 100000), flags, t).height())
                      for t, c in self._blocks(e)]
            laid.append((blocks, alpha, sum(h for _, _, h in blocks) + 2 * (len(blocks) - 1)))
        avail = rect.height() - (40 if self.edit_mode else 16)
        spacing = int(self.cfg["line_spacing"])
        while len(laid) > 1 and sum(h for _, _, h in laid) + spacing * (len(laid) - 1) > avail:
            laid.pop(0)  # drop the oldest line that doesn't fit

        y = 8
        for blocks, alpha, _ in laid:
            for text, color, h in blocks:
                shadow = QtGui.QColor(0, 0, 0, int(200 * alpha))
                for dx, dy in ((2, 2), (1, 1), (-1, 1)):
                    p.setPen(shadow)
                    p.drawText(QtCore.QRect(15 + dx, y + dy, width, h), flags, text)
                c = QtGui.QColor(color)
                c.setAlpha(int(255 * alpha))
                p.setPen(c)
                p.drawText(QtCore.QRect(15, y, width, h), flags, text)
                y += h + 2
            y += spacing
