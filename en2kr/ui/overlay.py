"""Transparent, click-through subtitle overlay drawn over the game.

Lines are keyed by line_id: the original appears as soon as it is recognised
and the translation fills in when it arrives. Long sentences wrap at the window
edge (v1 drew single lines), indented under their text rather than the marker.
A warning line at the top reports a Groq limit with a countdown.
"""
import ctypes
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..stt import fmt_wait

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
MAX_ENTRIES = 100
FADE_OUT_SEC = 1.0      # time to fade a line from opaque to invisible
COLLAPSE_SEC = 0.25     # then its space closes so the lines below slide up instead of jumping
FRAME_MS = 16           # repaint interval while something is animating (~60 fps)


def _smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def fade_state(age, fade_sec):
    """(alpha, height_factor) of a line ``age`` seconds after its last update; None once gone."""
    if fade_sec <= 0:
        return 1.0, 1.0
    t = age - fade_sec
    if t < 0:
        return 1.0, 1.0
    if t < FADE_OUT_SEC:
        return 1.0 - _smoothstep(t / FADE_OUT_SEC), 1.0
    if t < FADE_OUT_SEC + COLLAPSE_SEC:
        return 0.0, 1.0 - _smoothstep((t - FADE_OUT_SEC) / COLLAPSE_SEC)
    return None


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
        # Single-shot, re-armed after each paint: 16 ms while fading, otherwise just when the next fade starts.
        self._anim = QtCore.QTimer(self, singleShot=True, timeout=self.update)
        self._banner = None   # (message, until) while a Groq limit is in force
        self._banner_timer = QtCore.QTimer(self, interval=1000, timeout=self.update)  # countdown
        ov = settings.section("overlay")
        self.setGeometry(ov["x"], ov["y"], ov["w"], ov["h"])
        self._ensure_on_screen()
        self.apply_settings()

    # ---------- settings ----------
    def apply_settings(self):
        ov = self.settings.section("overlay")
        self.cfg = ov
        self.font = QtGui.QFont(ov.get("font_family") or "Malgun Gothic", int(ov["font_size"]), QtGui.QFont.Weight.Bold)
        if not ov.get("fade_sec", 0):
            self._anim.stop()
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

    def show_banner(self, message, seconds):
        """Warning line with a countdown; an empty message (or the countdown ending) removes it."""
        if message and seconds > 0:
            self._banner = (message, time.monotonic() + seconds)
            self._banner_timer.start()
        else:
            self._banner = None
            self._banner_timer.stop()
        self.update()

    def _banner_text(self, now=None):
        if not self._banner:
            return None
        message, until = self._banner
        left = until - (time.monotonic() if now is None else now)
        if left <= 0:
            self._banner = None
            self._banner_timer.stop()
            return None
        return f"⚠ {message} · {fmt_wait(left)} 후 재개"

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
    def _visible_entries(self, now=None):
        now = time.monotonic() if now is None else now
        fade = 0.0 if self.edit_mode else float(self.cfg.get("fade_sec", 0) or 0)
        items = list(self.entries.values())
        end = len(items) - self.scroll
        items = items[max(0, end - int(self.cfg["max_lines"])):end]
        out = []
        for e in items:
            state = fade_state(now - e.time, fade)
            if state is not None:
                out.append((e, *state))
        return out

    def _next_frame_ms(self, now=None):
        """Delay until the overlay needs repainting again, or None if nothing will change."""
        fade = float(self.cfg.get("fade_sec", 0) or 0)
        if fade <= 0 or self.edit_mode or not self.entries:
            return None
        now = time.monotonic() if now is None else now
        wait = None
        for e in self.entries.values():
            t = now - e.time - fade
            if 0 <= t < FADE_OUT_SEC + COLLAPSE_SEC:
                return FRAME_MS
            if t < 0:
                wait = -t if wait is None else min(wait, -t)
        return None if wait is None else max(FRAME_MS, int(wait * 1000) + 1)

    def _blocks(self, e):
        """(marker, text, color) per block; wrapped lines continue under the text, not the marker."""
        blocks = []
        if self.cfg.get("show_original", True):
            blocks.append(("● ", e.en, QtGui.QColor(255, 230, 80)))
        if e.error:
            blocks.append(("ㄴ ", f"⚠ {e.error[:120]}", QtGui.QColor(255, 150, 80)))
        elif e.kr is None:
            blocks.append(("ㄴ ", "…", QtGui.QColor(170, 170, 170)))
        else:
            blocks.append(("ㄴ ", e.kr, QtGui.QColor(255, 255, 255)))
        return blocks

    @staticmethod
    def _draw_text(p, rect, flags, text, color, alpha):
        shadow = QtGui.QColor(0, 0, 0, int(200 * alpha))
        for dx, dy in ((2, 2), (1, 1), (-1, 1)):
            p.setPen(shadow)
            p.drawText(rect.translated(dx, dy), flags, text)
        c = QtGui.QColor(color)
        c.setAlpha(int(255 * alpha))
        p.setPen(c)
        p.drawText(rect, flags, text)

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

        width = max(50, rect.width() - 45)
        flags = QtCore.Qt.TextFlag.TextWordWrap | QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        y = 8
        banner = self._banner_text()
        if banner:
            bfont = QtGui.QFont(self.font)
            bfont.setPointSize(max(9, int(self.cfg["font_size"] * 0.7)))
            p.setFont(bfont)
            bh = QtGui.QFontMetrics(bfont).boundingRect(QtCore.QRect(0, 0, width, 100000), flags, banner).height()
            self._draw_text(p, QtCore.QRect(15, y, width, bh), flags, banner, QtGui.QColor(255, 170, 60), 1.0)
            y += bh + 8

        p.setFont(self.font)
        fm = QtGui.QFontMetrics(self.font)
        laid = []
        spacing = int(self.cfg["line_spacing"])
        for e, alpha, grow in self._visible_entries():
            blocks = []
            for marker, text, color in self._blocks(e):
                indent = fm.horizontalAdvance(marker)
                h = fm.boundingRect(QtCore.QRect(0, 0, max(20, width - indent), 100000), flags, text).height()
                blocks.append((marker, indent, text, color, h))
            full = sum(b[4] + 2 for b in blocks) + spacing  # same as what the drawing loop advances
            laid.append((blocks, alpha, full * grow))
        avail = rect.height() - y - (32 if self.edit_mode else 8) + spacing
        while len(laid) > 1 and sum(h for _, _, h in laid) > avail:
            laid.pop(0)  # drop the oldest line that doesn't fit

        for blocks, alpha, space in laid:
            if alpha <= 0:  # collapsing: only its (shrinking) space remains
                y += round(space)
                continue
            for marker, indent, text, color, h in blocks:
                self._draw_text(p, QtCore.QRect(15, y, indent, h), flags, marker, color, alpha)
                self._draw_text(p, QtCore.QRect(15 + indent, y, max(20, width - indent), h), flags, text, color, alpha)
                y += h + 2
            y += spacing
        p.end()
        delay = self._next_frame_ms()
        if delay is None:
            self._anim.stop()
        else:
            self._anim.start(delay)
