"""Transparent, click-through subtitle overlay drawn over the game.

Lines are keyed by line_id: the original appears as soon as it is recognised
and the translation fills in when it arrives. How lines look is up to
SubtitleRenderer (subtitle_style.py), shared with the customise dialog's preview.
A warning line at the top reports a Groq limit with a countdown.

Edit mode makes the window clickable: drag to move (snapping to screen edges
and centre unless Alt is held), resize from the corner, and a small toolbar
offers font size, line count, background, quick placement and the customise window.
"""
import ctypes
import time
from collections import OrderedDict

from PySide6 import QtCore, QtGui, QtWidgets

from ..stt import fmt_wait
from .subtitle_style import PLACEMENTS, Entry, SubtitleRenderer, placed_rect, sample_entries, snap_rect

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
MAX_ENTRIES = 100
FADE_OUT_SEC = 1.0      # time to fade a line from opaque to invisible
COLLAPSE_SEC = 0.25     # then its space closes so the lines below slide up instead of jumping
FRAME_MS = 16           # repaint interval while something is animating (~60 fps)
BG_STEPS = (0, 30, 55, 80)  # toolbar's background button cycles through these (%)
TOAST_SEC = 1.4             # "번역 켜짐/꺼짐" notice after the toggle hotkey

TOOLBAR_STYLE = """
QFrame#overlayToolbar { background: rgba(20, 22, 27, 235); border: 1px solid #f2cc60; border-radius: 6px; }
QToolButton { background: #2a303a; color: #e6e6e6; border: 1px solid #353c48; border-radius: 4px;
              padding: 3px 7px; font-family: 'Malgun Gothic'; font-size: 9pt; }
QToolButton:hover { background: #343b47; }
QToolButton::menu-indicator { image: none; }
"""


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


class OverlayWindow(QtWidgets.QWidget):
    customize_requested = QtCore.Signal()
    edit_done = QtCore.Signal()

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
        self._preview = None  # style being edited in the customise window (shown with sample lines)
        self.cfg = settings.section("overlay")  # resize events may arrive before apply_settings()
        self.renderer = SubtitleRenderer()
        self.grip = QtWidgets.QSizeGrip(self)
        self.grip.setFixedSize(24, 24)
        self.grip.hide()
        self.toolbar = self._build_toolbar()
        # Single-shot, re-armed after each paint: 16 ms while fading, otherwise just when the next fade starts.
        self._anim = QtCore.QTimer(self, singleShot=True, timeout=self.update)
        self._banner = None   # (message, until) while a Groq limit is in force
        self._banner_timer = QtCore.QTimer(self, interval=1000, timeout=self.update)  # countdown
        self.translating = True
        self._toast = None    # (text, color, until)
        self._toast_timer = QtCore.QTimer(self, singleShot=True, timeout=self.update)
        ov = settings.section("overlay")
        if ov.get("x") is None or ov.get("y") is None:   # first start: bottom centre of the main screen
            self.resize(int(ov["w"]), int(ov["h"]))
            self.place("bottom", QtGui.QGuiApplication.primaryScreen())
        else:
            self.setGeometry(ov["x"], ov["y"], ov["w"], ov["h"])
        self._ensure_on_screen()
        self.apply_settings()

    # ---------- settings ----------
    def apply_settings(self):
        self.cfg = dict(self._preview) if self._preview is not None else self.settings.section("overlay")
        self.renderer.set_style(self.cfg)
        if not self.cfg.get("fade_sec", 0):
            self._anim.stop()
        self._place_toolbar()
        self.update()

    @property
    def previewing(self):
        return self._preview is not None

    def set_style_preview(self, cfg):
        """Show ``cfg`` (unsaved, with sample lines) while the customise window is open; None ends it."""
        self._preview = dict(cfg) if cfg is not None else None
        self.apply_settings()

    def _set(self, key, value):
        """Change one overlay setting from edit mode (also inside a running preview)."""
        self.settings.set(f"overlay.{key}", value)
        if self._preview is not None:
            self._preview[key] = value
        self.cfg[key] = value
        self.renderer.set_style(self.cfg)
        self.update()

    def _save_geometry(self):
        g = self.geometry()
        for k, v in (("x", g.x()), ("y", g.y()), ("w", g.width()), ("h", g.height())):
            self.settings.set(f"overlay.{k}", v)
            self.cfg[k] = v
        self.settings.save()

    def _ensure_on_screen(self):
        screens = QtGui.QGuiApplication.screens()
        if screens and not any(s.availableGeometry().intersects(self.geometry()) for s in screens):
            self.move(100, 100)

    def place(self, where, screen=None):
        """Quick placement (see PLACEMENTS) on ``screen`` (default: the one the overlay is on)."""
        screen = screen or self.screen() or QtGui.QGuiApplication.primaryScreen()
        self.setGeometry(placed_rect(where, self.size(), screen.geometry()))
        self._save_geometry()

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

    def set_translating(self, on):
        """Translation switched on/off: brief notice in the middle."""
        self.translating = bool(on)
        text, color = ("번역 켜짐", QtGui.QColor(110, 220, 140)) if on else ("번역 꺼짐", QtGui.QColor(200, 200, 200))
        self._toast = (text, color, time.monotonic() + TOAST_SEC)
        self._toast_timer.start(int(TOAST_SEC * 1000) + 20)
        self.update()

    def _paint_toast(self, p, rect):
        if not self._toast:
            return
        text, color, until = self._toast
        if time.monotonic() >= until:
            self._toast = None
            return
        font = QtGui.QFont(self.renderer.font)
        font.setPointSize(max(11, int(self.cfg["font_size"] * 0.8)))
        fm = QtGui.QFontMetrics(font)
        w, h = fm.horizontalAdvance(text) + 36, fm.height() + 14
        box = QtCore.QRect((rect.width() - w) // 2, max(6, (rect.height() - h) // 2), w, h)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(15, 17, 21, 210))
        p.drawRoundedRect(box, h / 2, h / 2)
        p.setFont(font)
        p.setPen(color)
        p.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, text)

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
    def _build_toolbar(self):
        bar = QtWidgets.QFrame(self)
        bar.setObjectName("overlayToolbar")
        bar.setStyleSheet(TOOLBAR_STYLE)
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(3)

        def button(text, tip, slot):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        button("가−", "글자 작게 (-)", lambda: self.change_font(-2))
        button("가+", "글자 크게 (=)", lambda: self.change_font(2))
        button("줄−", "표시 줄 수 줄이기 (Ctrl+휠)", lambda: self.change_max_lines(-1))
        button("줄+", "표시 줄 수 늘리기 (Ctrl+휠)", lambda: self.change_max_lines(1))
        button("배경", "배경 어둡게: 투명 → 30% → 55% → 80%", self.cycle_background)
        where = QtWidgets.QToolButton()
        where.setText("위치 ▾")
        where.setToolTip("화면의 정해진 자리로 옮기기")
        where.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(where)
        for key, label in PLACEMENTS.items():
            menu.addAction(label, lambda k=key: self.place(k))
        where.setMenu(menu)
        lay.addWidget(where)
        button("꾸미기", "오버레이 꾸미기 창 열기", self.customize_requested.emit)
        button("완료", "편집 모드 끝내기", self.edit_done.emit)
        bar.adjustSize()
        bar.hide()
        return bar

    def _place_toolbar(self):
        bar = self.toolbar
        bar.adjustSize()
        bar.move(max(4, self.width() - bar.width() - 6), 6)
        bar.setVisible(self.edit_mode and bool(self.cfg.get("toolbar", True)))
        if bar.isVisible():
            bar.raise_()

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
        self._place_toolbar()
        self.update()
        return self.edit_mode

    def change_font(self, delta):
        if self.edit_mode:
            self._set("font_size", max(8, min(72, int(self.cfg["font_size"]) + delta)))

    def change_max_lines(self, delta):
        self._set("max_lines", max(1, min(20, int(self.cfg["max_lines"]) + delta)))

    def change_spacing(self, delta):
        self._set("line_spacing", max(0, min(40, int(self.cfg["line_spacing"]) + delta)))

    def cycle_background(self):
        now = int(self.cfg.get("bg_opacity", 0))
        self._set("bg_opacity", next((s for s in BG_STEPS if s > now), BG_STEPS[0]))
        self.settings.save()

    # ---------- mouse ----------
    def mousePressEvent(self, event):
        if self.edit_mode and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        rect = QtCore.QRect(event.globalPosition().toPoint() - self._drag, self.size())
        if self.cfg.get("snap", True) and not event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier:
            rect = snap_rect(rect, [s.geometry() for s in QtGui.QGuiApplication.screens()])
        self.move(rect.topLeft())

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
        self._place_toolbar()
        super().resizeEvent(event)

    # ---------- painting ----------
    def _visible_entries(self, now=None):
        if self._preview is not None:
            return [(e, 1.0, 1.0) for e in sample_entries(int(self.cfg["max_lines"]))]
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
        if fade <= 0 or self.edit_mode or not self.entries or self._preview is not None:
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

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        bg = int(float(self.cfg.get("bg_opacity", 0)) * 2.55)
        if self.edit_mode:
            bg = max(bg, 160)
        if bg:
            p.fillRect(rect, QtGui.QColor(0, 0, 0, bg))
        if self.edit_mode:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 220, 0), 2))
            p.drawRect(rect.adjusted(1, 1, -1, -1))
            info = (f"편집 모드 · 끌어서 이동 (Alt: 자석 끔) · 모서리로 크기 · 글자 {self.cfg['font_size']}pt(=/-) · "
                    f"줄 {self.cfg['max_lines']}(Ctrl+휠) · 간격 {self.cfg['line_spacing']}px(Shift+휠)")
            p.setFont(QtGui.QFont("Malgun Gothic", 9, QtGui.QFont.Weight.Bold))
            r = QtCore.QRect(8, rect.height() - 26, rect.width() - 40, 20)
            p.setPen(QtGui.QColor(255, 220, 0))
            p.drawText(r, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, info)

        self.renderer.dpr = self.devicePixelRatioF()
        top = self.toolbar.geometry().bottom() + 8 if self.toolbar.isVisible() else 8  # keep text clear of it
        self.renderer.paint(p, rect, self._visible_entries(), banner=self._banner_text(), speaking=self.speaking,
                            top=top, bottom_reserve=32 if self.edit_mode else 8)
        self._paint_toast(p, rect)
        p.end()
        delay = self._next_frame_ms()
        if delay is None:
            self._anim.stop()
        else:
            self._anim.start(delay)
