"""Overlay customisation: style checks, placement helpers and the shared renderer."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from en2kr.settings import OVERLAY_STYLE_DEFAULTS  # noqa: E402
from en2kr.ui import subtitle_style as ss  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_clean_style_keeps_only_valid_values():
    got = ss.clean_style({
        "font_size": 500, "bold": "yes", "original_color": "#ABCDEF", "translation_color": "red",
        "effect": "glow", "align": "center", "original_marker": "  ▶▶▶▶  ", "unknown": 1,
        "x": 10, "box_opacity": 42.6,
    })
    assert got == {"font_size": 72, "original_color": "#abcdef", "align": "center",
                   "original_marker": "▶▶▶", "box_opacity": 43}
    assert ss.clean_style("not a dict") == {}


def test_presets_are_complete_and_valid():
    for name, preset in ss.PRESETS.items():
        assert ss.clean_style(preset) == preset, name


def test_snap_to_screen_edges_and_centre():
    screen = QtCore.QRect(0, 0, 1920, 1080)
    near_left = ss.snap_rect(QtCore.QRect(10, 500, 800, 200), [screen])
    assert near_left.left() == 0 and near_left.top() == 500
    centred = ss.snap_rect(QtCore.QRect(1920 // 2 - 400 + 9, 870, 800, 200), [screen])
    assert centred.center().x() == screen.center().x()
    assert ss.snap_rect(QtCore.QRect(300, 300, 800, 200), [screen]) == QtCore.QRect(300, 300, 800, 200)


def test_quick_placement_stays_on_the_chosen_screen():
    second = QtCore.QRect(1920, 0, 2560, 1440)
    size = QtCore.QSize(800, 250)
    bottom = ss.placed_rect("bottom", size, second)
    assert second.contains(bottom) and bottom.center().x() == pytest.approx(second.center().x(), abs=1)
    assert bottom.bottom() < second.bottom() - 100           # above the bottom HUD
    assert ss.placed_rect("top", size, second).top() < second.top() + 100
    assert ss.placed_rect("bottom_left", size, second).left() < second.left() + 100
    assert ss.placed_rect("bottom_right", size, second).right() > second.right() - 100
    huge = ss.placed_rect("center", QtCore.QSize(5000, 5000), second)
    assert second.contains(huge)


def _ink_rows(cfg, entries=None, size=(700, 320)):
    """Rows of a rendered image that contain any visible pixel."""
    r = ss.SubtitleRenderer({**cfg, "show_speaking": False})
    img = QtGui.QImage(size[0], size[1], QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(img)
    items = [(e, 1.0, 1.0) for e in (entries or ss.sample_entries()[:1])]
    r.paint(p, QtCore.QRect(0, 0, *size), items)
    p.end()
    return [y for y in range(size[1]) if any(img.pixelColor(x, y).alpha() > 0 for x in range(0, size[0], 3))]


def test_every_preset_and_effect_renders(app):
    for preset in ss.PRESETS.values():
        assert _ink_rows(preset)
    for effect in ("shadow", "outline", "none"):
        assert _ink_rows({"effect": effect, "box": True})


def test_anchor_bottom_puts_lines_at_the_bottom(app):
    top = _ink_rows({"anchor": "top"})
    bottom = _ink_rows({"anchor": "bottom"})
    assert max(top) < 160 and min(bottom) > 160


def test_newest_first_puts_the_last_line_on_top(app):
    first, second = ss.sample_entries()[:2]
    r = ss.SubtitleRenderer({"newest_first": True, "show_original": False, "translation_marker": "",
                             "show_speaking": False})
    blocks = {}

    def fake_render(text, *args, **kwargs):
        blocks[text] = ss.Block(QtGui.QPixmap(1, 1), 20, 0, [])
        return blocks[text]

    class Painter:  # records where each block is drawn
        def __init__(self):
            self.drawn = []

        def drawPixmap(self, point, pixmap):
            self.drawn.append((point.y(), next(t for t, b in blocks.items() if b.pixmap is pixmap)))

        def __getattr__(self, name):
            return lambda *a, **k: None

    r._render = fake_render
    painter = Painter()
    r.paint(painter, QtCore.QRect(0, 0, 600, 300), [(first, 1.0, 1.0), (second, 1.0, 1.0)])
    assert [t for _, t in sorted(painter.drawn)] == [second.kr, first.kr]


def test_translation_first_and_hidden_original():
    r = ss.SubtitleRenderer({"translation_first": True})
    e = ss.sample_entries()[0]
    assert [b[1] for b in r.blocks(e)] == [e.kr, e.en]
    r.set_style({"show_original": False})
    assert [b[1] for b in r.blocks(e)] == [e.kr]


def test_outline_paint_stays_cheap(app):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    r = ss.SubtitleRenderer({"effect": "outline", "effect_size": 4, "box": True})
    img = QtGui.QImage(900, 500, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    entries = [ss.Entry("A fairly long sentence that has to wrap onto a second line " * 2,
                        "오버레이에서 두 줄로 넘어가야 하는 꽤 긴 문장입니다 " * 2, None, "en", 0) for _ in range(8)]
    items = [(e, 0.5, 1.0) for e in entries]
    p = QtGui.QPainter(img)
    r.paint(p, img.rect(), items)        # first paint renders and caches the blocks
    frames = []
    for _ in range(30):
        t0 = time.perf_counter()
        r.paint(p, img.rect(), items)
        frames.append((time.perf_counter() - t0) * 1000)
    p.end()
    assert sorted(frames)[15] < 8, frames


def test_overlay_preview_shows_samples_and_restores(app, tmp_path):
    from en2kr.settings import Settings
    from en2kr.ui.overlay import OverlayWindow
    s = Settings(str(tmp_path / "s.json")).load()
    ov = OverlayWindow(s)
    ov.set_style_preview({**OVERLAY_STYLE_DEFAULTS, "align": "right"})
    assert len(ov._visible_entries()) == len(ss.SAMPLE_LINES) and ov.renderer.cfg["align"] == "right"
    ov.set_style_preview(None)
    assert ov._visible_entries() == [] and ov.renderer.cfg["align"] == OVERLAY_STYLE_DEFAULTS["align"]
    ov.close()


def test_non_finite_numbers_are_ignored():
    assert ss.clean_style({"box_opacity": float("nan"), "font_size": float("inf"), "max_lines": 3}) == {"max_lines": 3}


def test_cache_stays_small_and_pixmaps_fit_their_text(app):
    r = ss.SubtitleRenderer({"effect": "outline", "font_size": 24})
    r.dpr = 1.5
    short = r._render("Go.", r.font, QtGui.QColor("#ffffff"), 1500, "center")
    assert short.pixmap.width() < 300 * 1.5        # cropped to the word, not the 1500 px text area
    for i in range(400):
        r._render(f"A long line number {i} that keeps the cache busy " * 3, r.font, QtGui.QColor("#ffffff"), 1500, "left")
    assert r._cache_bytes <= ss.CACHE_BYTES


def test_previews_follow_the_line_count():
    assert len(ss.sample_entries(1)) == 1 and len(ss.sample_entries()) == len(ss.SAMPLE_LINES)


def test_first_start_places_the_overlay_at_the_bottom_centre(app, tmp_path):
    from en2kr.settings import Settings
    from en2kr.ui.overlay import OverlayWindow
    s = Settings(str(tmp_path / "s.json")).load()
    ov = OverlayWindow(s)
    screen = QtGui.QGuiApplication.primaryScreen().geometry()
    assert abs(ov.geometry().center().x() - screen.center().x()) <= 2
    assert ov.geometry().bottom() > screen.center().y()
    assert isinstance(s.get("overlay.x"), int)               # saved, so the next start keeps it
    ov.close()
