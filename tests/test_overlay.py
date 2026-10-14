import os
import time

import numpy as np
import pytest

from en2kr.ui.overlay import COLLAPSE_SEC, FADE_OUT_SEC, FRAME_MS, fade_state


def test_fade_is_smooth_and_continuous():
    fade = 5.0
    ages = np.arange(0, fade + FADE_OUT_SEC + COLLAPSE_SEC, 0.016)
    states = [fade_state(a, fade) for a in ages]
    alphas = [s[0] for s in states]
    heights = [s[1] for s in states]
    assert all(b <= a + 1e-9 for a, b in zip(alphas, alphas[1:]))           # never brightens
    assert max(abs(b - a) for a, b in zip(alphas, alphas[1:])) < 0.05        # no visible steps at 60 fps
    assert max(abs(b - a) for a, b in zip(heights, heights[1:])) < 0.2       # space closes gradually
    assert fade_state(fade + FADE_OUT_SEC + COLLAPSE_SEC + 0.01, fade) is None
    assert fade_state(100, 0) == (1.0, 1.0)                                  # fading disabled


@pytest.fixture
def overlay(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from en2kr.settings import Settings
    from en2kr.ui.overlay import OverlayWindow
    s = Settings(str(tmp_path / "s.json")).load()
    s.set("overlay.fade_sec", 5)
    ov = OverlayWindow(s)
    yield ov, app
    ov.close()


def test_repaint_schedule(overlay):
    ov, _ = overlay
    now = time.monotonic()
    assert ov._next_frame_ms(now) is None                      # nothing to show
    ov.upsert_source(1, "hello", "en")
    ov.entries[1].time = now - 2.0
    assert 2900 <= ov._next_frame_ms(now) <= 3100              # idle until the fade starts
    ov.entries[1].time = now - 5.5
    assert ov._next_frame_ms(now) == FRAME_MS                  # animating
    ov.entries[1].time = now - 60
    assert ov._next_frame_ms(now) is None                      # gone
    ov.edit_mode = True
    ov.entries[1].time = now - 5.5
    assert ov._next_frame_ms(now) is None                      # edit mode shows everything, no fading


def test_paint_is_cheap_enough_for_60fps(overlay):
    ov, app = overlay
    ov.resize(900, 400)
    for i in range(8):
        ov.upsert_source(i, "A fairly long sentence that has to wrap onto a second line in the overlay " * 2, "en")
        ov.set_translation(i, ov.entries[i].en, "오버레이에서 두 줄로 넘어가야 하는 꽤 긴 문장입니다 " * 2, None)
    ov.grab()  # warm-up: font loading and glyph caches
    frames = []
    for _ in range(30):
        t0 = time.perf_counter()
        ov.grab()
        frames.append((time.perf_counter() - t0) * 1000)
    median = sorted(frames)[len(frames) // 2]
    print(f"paint median {median:.2f} ms, max {max(frames):.2f} ms")
    assert median < 12, frames
