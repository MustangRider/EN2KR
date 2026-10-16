"""How subtitles look: presets, style checks, placement helpers and the renderer
shared by the overlay and the customise dialog's preview.

Each block (one original or translated sentence) is laid out with QTextLayout and
drawn once into a cached pixmap together with its shadow/outline, so fading lines
at 60 fps only blit pixmaps, whatever effect is chosen.
"""
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore, QtGui

from ..settings import OVERLAY_CLASSIC_LOOK, OVERLAY_STYLE_DEFAULTS

LEFT_MARGIN = 15
DOT_MARGIN = 30          # right margin that keeps text clear of the speaking dot
SNAP_PX = 16
CACHE_BYTES = 32 * 2 ** 20   # rendered blocks kept for repainting (cropped to their text)
PENDING_COLOR = "#aaaaaa"
BANNER_COLOR = "#ffaa3c"

CHOICES = {"effect": ("shadow", "outline", "none"), "align": ("left", "center", "right"), "anchor": ("top", "bottom")}
RANGES = {"font_size": (8, 72), "line_spacing": (0, 40), "max_lines": (1, 20), "fade_sec": (0, 120),
          "bg_opacity": (0, 80), "text_opacity": (30, 100), "effect_size": (1, 6), "box_opacity": (0, 100),
          "original_scale": (50, 150)}
MARKER_MAX = 3
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Presets set the look only; size, line count, spacing, fading and position stay as they are.
_LOOK = ("bold", "show_original", "original_color", "translation_color", "error_color", "text_opacity",
         "effect", "effect_color", "effect_size", "box", "box_color", "box_opacity", "original_scale",
         "original_marker", "translation_marker", "align", "anchor", "newest_first", "translation_first")
_BASE = {k: OVERLAY_STYLE_DEFAULTS[k] for k in _LOOK}
PRESETS = {
    "유튜브 자막": dict(_BASE),                       # the default look
    "클래식": {**_BASE, **OVERLAY_CLASSIC_LOOK},       # v2.0-2.2.1
    "번역만": {**_BASE, "show_original": False, "bold": True, "effect": "outline", "effect_size": 3,
            "box": False},
    "고대비": {**_BASE, "bold": True, "original_color": "#7fe3ff", "translation_color": "#ffff3c",
            "effect": "outline", "effect_size": 3, "box_opacity": 80, "original_scale": 90},
}
PLACEMENTS = {"top": "상단 가운데", "center": "화면 가운데", "bottom_left": "왼쪽 아래",
              "bottom": "하단 가운데", "bottom_right": "오른쪽 아래"}
SAMPLE_LINES = [("Enemy on the left, behind the red building.", "왼쪽에 적이 있어요, 빨간 건물 뒤쪽입니다."),
                ("Don't shoot, we are friendly.", "쏘지 마세요, 우리는 우호적이에요."),
                ("Let's move together.", None)]


@dataclass
class Entry:
    en: str
    kr: Optional[str]
    error: Optional[str]
    lang: str
    time: float


def sample_entries(max_lines=None):
    now = time.monotonic()
    lines = SAMPLE_LINES[-max_lines:] if max_lines else SAMPLE_LINES
    return [Entry(en, kr, None, "en", now) for en, kr in lines]


def clean_style(data):
    """The valid style values in ``data`` (an imported file or old settings), clamped to their ranges."""
    out = {}
    if not isinstance(data, dict):
        return out
    for key, default in OVERLAY_STYLE_DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(default, int):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                lo, hi = RANGES.get(key, (-10 ** 6, 10 ** 6))
                out[key] = int(min(max(round(value), lo), hi))
        elif isinstance(value, str):
            if key.endswith("_color"):
                if _HEX.match(value):
                    out[key] = value.lower()
            elif key in CHOICES:
                if value in CHOICES[key]:
                    out[key] = value
            elif key.endswith("_marker"):
                out[key] = value.strip()[:MARKER_MAX]
            elif value.strip():
                out[key] = value.strip()
    return out


def snap_rect(rect, areas, dist=SNAP_PX):
    """Shift rect so an edge or its centre within ``dist`` of a screen edge or centre lines up with it."""
    best = [None, None]
    near = rect.adjusted(-dist, -dist, dist, dist)
    for a in areas:
        if not a.intersects(near):
            continue
        for axis, pairs in ((0, ((a.left(), rect.left()), (a.right(), rect.right()), (a.center().x(), rect.center().x()))),
                            (1, ((a.top(), rect.top()), (a.bottom(), rect.bottom()), (a.center().y(), rect.center().y())))):
            for target, edge in pairs:
                d = target - edge
                if abs(d) <= dist and (best[axis] is None or abs(d) < abs(best[axis])):
                    best[axis] = d
    return rect.translated(best[0] or 0, best[1] or 0)


def placed_rect(where, size, area):
    """The overlay's rectangle for a quick placement on a screen (subtitles sit above the bottom HUD)."""
    w, h = min(size.width(), area.width()), min(size.height(), area.height())
    side = int(area.width() * 0.03)
    bottom = int(area.height() * 0.10)
    x = {"bottom_left": area.x() + side,
         "bottom_right": area.x() + area.width() - w - side}.get(where, area.x() + (area.width() - w) // 2)
    y = {"top": area.y() + int(area.height() * 0.04),
         "center": area.y() + (area.height() - h) // 2}.get(where, area.y() + area.height() - h - bottom)
    return QtCore.QRect(x, y, w, h)


def _color(value, fallback):
    return QtGui.QColor(value if isinstance(value, str) and _HEX.match(value) else fallback)


@dataclass
class Block:
    pixmap: QtGui.QPixmap
    height: int
    pad: int
    lines: list           # [(x, y, width, height)] of each laid-out line, relative to the text origin
    left: float = 0.0     # where the pixmap's text area starts, relative to the text origin


class SubtitleRenderer:
    def __init__(self, cfg=None):
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self.dpr = 1.0
        self.set_style(cfg or {})

    def set_style(self, cfg):
        self.cfg = {**OVERLAY_STYLE_DEFAULTS, **clean_style(cfg)}
        c = self.cfg
        weight = QtGui.QFont.Weight.Bold if c["bold"] else QtGui.QFont.Weight.Normal
        self.font = QtGui.QFont(c["font_family"] or "Malgun Gothic", int(c["font_size"]), weight)
        self.original_font = QtGui.QFont(self.font)
        self.original_font.setPointSizeF(max(6.0, c["font_size"] * c["original_scale"] / 100))
        self._cache.clear()
        self._cache_bytes = 0

    # ---------- blocks ----------
    def blocks(self, e):
        """(marker, text, color, font) for each block of an entry, in display order."""
        c = self.cfg
        marker = c["translation_marker"]
        if e.error:
            tr = (marker, "⚠ " + " ".join(e.error[:120].split()), _color(c["error_color"], "#ff9650"), self.font)
        elif e.kr is None:
            tr = (marker, "…", QtGui.QColor(PENDING_COLOR), self.font)
        else:
            tr = (marker, e.kr, _color(c["translation_color"], "#ffffff"), self.font)
        out = [tr]
        if c["show_original"]:
            orig = (c["original_marker"], e.en, _color(c["original_color"], "#ffe650"), self.original_font)
            out = [tr, orig] if c["translation_first"] else [orig, tr]
        return out

    def _render(self, text, font, color, width, align):
        c = self.cfg
        effect, size = c["effect"], int(c["effect_size"])
        effect_color = _color(c["effect_color"], "#000000")
        key = (text, font.key(), color.rgba(), width, align, effect, effect_color.rgba(), size, self.dpr)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit

        layout = QtGui.QTextLayout(text, font)
        opt = QtGui.QTextOption()
        opt.setWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        fm = QtGui.QFontMetricsF(font)
        lines, y = [], 0.0
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QtCore.QPointF(0, y))
            part = text[line.textStart():line.textStart() + line.textLength()].rstrip()
            natural = fm.horizontalAdvance(part)
            x = {"center": (width - natural) / 2, "right": width - natural}.get(align, 0.0)
            lines.append((part, x, y, natural, line.height(), line.ascent()))
            y += line.height()
        layout.endLayout()

        pad = size + 2 if effect != "none" else 2
        height = max(1, math.ceil(y))
        left = math.floor(min((x for _, x, *_ in lines), default=0.0))
        span = max(1, math.ceil(max((x + natural for _, x, _, natural, _, _ in lines), default=1.0)) - left)
        pm = QtGui.QPixmap(math.ceil((span + 2 * pad) * self.dpr), math.ceil((height + 2 * pad) * self.dpr))
        pm.setDevicePixelRatio(self.dpr)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        p.setFont(font)
        if effect == "outline":
            path = QtGui.QPainterPath()
            for part, x, ly, _, _, ascent in lines:
                path.addText(QtCore.QPointF(pad + x - left, pad + ly + ascent), font, part)
            pen = QtGui.QPen(effect_color, 2 * size)
            pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
            p.strokePath(path, pen)
            p.fillPath(path, color)
        else:
            passes = []
            if effect == "shadow":
                shadow = QtGui.QColor(effect_color)
                shadow.setAlpha(200)
                passes = [(d, d, shadow) for d in range(1, size + 1)] + [(-1, 1, shadow)]
            passes.append((0, 0, color))
            for dx, dy, col in passes:
                p.setPen(col)
                for part, x, ly, _, _, ascent in lines:
                    p.drawText(QtCore.QPointF(pad + x - left + dx, pad + ly + ascent + dy), part)
        p.end()
        block = Block(pm, height, pad, [(x, ly, natural, lh) for _, x, ly, natural, lh, _ in lines], left)
        self._cache[key] = block
        self._cache_bytes += pm.width() * pm.height() * 4
        while self._cache_bytes > CACHE_BYTES and len(self._cache) > 1:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.pixmap.width() * old.pixmap.height() * 4
        return block

    # ---------- painting ----------
    def text_width(self, rect):
        right = DOT_MARGIN if self.cfg["show_speaking"] else LEFT_MARGIN
        return max(50, rect.width() - LEFT_MARGIN - right)

    def paint(self, p, rect, items, banner=None, speaking=False, top=8, bottom_reserve=8):
        """items: [(entry, alpha, height_factor)] oldest first (see overlay.fade_state)."""
        c = self.cfg
        if c["show_speaking"]:
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(255, 60, 60) if speaking else QtGui.QColor(110, 110, 110, 180))
            p.drawEllipse(rect.width() - 22, 10, 10, 10)
        width = self.text_width(rect)
        align = c["align"]
        y = top
        if banner:
            bfont = QtGui.QFont(self.font)
            bfont.setPointSize(max(9, int(c["font_size"] * 0.7)))
            b = self._render(banner, bfont, QtGui.QColor(BANNER_COLOR), width, align)
            p.drawPixmap(QtCore.QPointF(LEFT_MARGIN + b.left - b.pad, y - b.pad), b.pixmap)
            y += b.height + 8

        spacing = int(c["line_spacing"])
        laid = []
        for e, alpha, grow in items:
            rendered = []
            for marker, text, color, font in self.blocks(e):
                if marker and align == "left":   # hanging indent: wrapped lines continue under the text
                    mark = marker + " "
                    indent = math.ceil(QtGui.QFontMetricsF(font).horizontalAdvance(mark))
                    rendered.append((self._render(mark, font, color, indent + 2, "left"), indent,
                                     self._render(text, font, color, max(20, width - indent), "left")))
                else:
                    rendered.append((None, 0, self._render(f"{marker} {text}" if marker else text, font, color,
                                                           width, align)))
            full = sum(body.height + 2 for _, _, body in rendered) + spacing
            laid.append((rendered, alpha, full * grow))
        avail = rect.height() - y - bottom_reserve + spacing
        while len(laid) > 1 and sum(h for _, _, h in laid) > avail:
            laid.pop(0)  # drop the oldest line that doesn't fit
        if c["newest_first"]:
            laid.reverse()
        if c["anchor"] == "bottom":
            y = max(y, round(rect.height() - bottom_reserve + spacing - sum(h for _, _, h in laid)))

        box = _color(c["box_color"], "#000000") if c["box"] else None
        text_opacity = c["text_opacity"] / 100
        for rendered, alpha, space in laid:
            if alpha <= 0:  # collapsing: only its (shrinking) space remains
                y += round(space)
                continue
            for mark, indent, body in rendered:
                if box is not None:
                    fill = QtGui.QColor(box)
                    fill.setAlpha(int(255 * c["box_opacity"] / 100 * alpha))
                    p.setPen(QtCore.Qt.PenStyle.NoPen)
                    p.setBrush(fill)
                    last = len(body.lines) - 1
                    for i, (lx, ly, lw, lh) in enumerate(body.lines):
                        left = LEFT_MARGIN + (0 if i == 0 and mark is not None else indent + lx) - 6
                        right = LEFT_MARGIN + indent + lx + lw + 6
                        top = y + ly - (1 if i == 0 else 0)            # lines touch without overlapping,
                        bottom = y + ly + lh + (1 if i == last else 0)  # so the box stays one shade
                        p.drawRoundedRect(QtCore.QRectF(left, top, right - left, bottom - top), 4, 4)
                p.setOpacity(alpha * text_opacity)
                if mark is not None:
                    p.drawPixmap(QtCore.QPointF(LEFT_MARGIN + mark.left - mark.pad, y - mark.pad), mark.pixmap)
                p.drawPixmap(QtCore.QPointF(LEFT_MARGIN + indent + body.left - body.pad, y - body.pad), body.pixmap)
                p.setOpacity(1.0)
                y += body.height + 2
            y += spacing
