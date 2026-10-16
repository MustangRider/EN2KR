"""Translation on/off: the modifier-tap hotkey, the chimes and the pipeline switch."""
import io
import wave

import numpy as np

from en2kr import sounds
from en2kr.pipeline import Pipeline, PipelineEvents
from en2kr.settings import Settings
from en2kr.ui.hotkeys import ChordTap

CTRL, RCTRL, ALT, O = 29, 57629, 56, 24


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def tap_detector():
    clock, fired = Clock(), []
    tap = ChordTap([(CTRL, RCTRL), (ALT, 57400)], lambda: fired.append(clock.t), clock=clock)

    def press(*events):
        for code, down, dt in events:
            clock.t += dt
            tap.feed(code, down)
    return press, fired


def test_quick_ctrl_alt_tap_fires():
    press, fired = tap_detector()
    press((CTRL, True, 0), (ALT, True, 0.05), (ALT, False, 0.12), (CTRL, False, 0.02))
    assert len(fired) == 1
    press((RCTRL, True, 1), (ALT, True, 0.03), (RCTRL, False, 0.1), (ALT, False, 0.01))  # either order, right Ctrl too
    assert len(fired) == 2


def test_ctrl_alt_o_does_not_toggle():
    press, fired = tap_detector()
    press((CTRL, True, 0), (ALT, True, 0.05), (O, True, 0.1), (O, False, 0.05), (ALT, False, 0.05), (CTRL, False, 0.01))
    assert fired == []


def test_holding_ctrl_then_alt_does_not_toggle():
    press, fired = tap_detector()
    press((CTRL, True, 0), (CTRL, True, 0.5), (ALT, True, 1.5), (ALT, False, 0.1), (CTRL, False, 0.3))  # crouch + walk
    assert fired == []


def test_long_hold_and_repeat_do_not_toggle():
    press, fired = tap_detector()
    press((CTRL, True, 0), (ALT, True, 0.05), (ALT, True, 0.5), (ALT, True, 0.5), (ALT, False, 0.5), (CTRL, False, 0))
    assert fired == []
    press((CTRL, True, 1), (ALT, True, 0.05), (ALT, False, 0.1), (CTRL, False, 0.05))  # recovers right after
    assert len(fired) == 1


def test_lost_key_release_does_not_block_forever():
    press, fired = tap_detector()
    press((CTRL, True, 0))                         # its release never arrives
    press((CTRL, True, 60), (ALT, True, 0.05), (ALT, False, 0.1), (CTRL, False, 0.05))
    assert len(fired) == 1


def _pitch(samples, start, end):
    seg = samples[int(start * sounds.SAMPLE_RATE):int(end * sounds.SAMPLE_RATE)]
    seg = seg * np.hanning(len(seg))
    return np.fft.rfftfreq(len(seg), 1 / sounds.SAMPLE_RATE)[np.argmax(np.abs(np.fft.rfft(seg)))]


def test_beeps_rise_and_fall_without_clicks():
    on, off = sounds.chime(True, 0.5), sounds.chime(False, 0.5)
    assert _pitch(on, 0.0, 0.07) < _pitch(on, 0.11, 0.18)
    assert _pitch(off, 0.0, 0.07) > _pitch(off, 0.11, 0.18)
    for s in (on, off):
        assert 0.1 < np.max(np.abs(s)) <= sounds.PEAK * 0.5 + 1e-6   # well below full scale
        assert abs(s[0]) < 1e-3 and abs(s[-1]) < 1e-2                # no click at either end
        assert len(s) / sounds.SAMPLE_RATE < 0.25                    # short
    assert np.max(np.abs(sounds.chime(True, 1.0))) > np.max(np.abs(on))


def test_chime_file_is_a_valid_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(sounds.paths, "app_dir", lambda: str(tmp_path))
    path = sounds.chime_path(True, 40)
    with wave.open(path) as w:
        assert w.getframerate() == sounds.SAMPLE_RATE and w.getnchannels() == 1 and w.getnframes() > 5000
    assert sounds.chime_path(True, 40) == path    # cached per volume
    with wave.open(io.BytesIO(sounds.wav_bytes(sounds.chime(False)))) as w:
        assert w.getsampwidth() == 2


def test_switching_off_stops_recognition(tmp_path):
    seen = {"status": [], "speech": []}
    p = Pipeline(Settings(str(tmp_path / "s.json")).load(),
                 PipelineEvents(status=lambda c, s, m: seen["status"].append((c, s, m)),
                                speech=seen["speech"].append))
    p._seg_q.put((np.zeros(16000, np.float32), {"start_sample": 0, "end_sample": 16000}))
    p._backlog.append((np.zeros(16000, np.float32), {"start_sample": 0, "end_sample": 16000}))
    p.set_enabled(False)
    assert p._seg_q.empty() and seen["speech"] == [False] and ("stt", "off", "번역 꺼짐") in seen["status"]
    p.engine.cloud = object()   # pretend an engine is ready; nothing may reach it while off
    p._running = True
    import threading
    t = threading.Thread(target=p._stt_loop, daemon=True)
    t.start()
    p._seg_q.put((np.zeros(16000, np.float32), {"start_sample": 0, "end_sample": 16000}))
    import time
    time.sleep(0.6)
    p._running = False
    t.join(2)
    assert p._seg_q.empty() and not p._backlog


def test_toggle_defaults(tmp_path):
    s = Settings(str(tmp_path / "s.json")).load()
    assert s.get("hotkeys.translate_toggle") == "ctrl+alt" and s.get("sound.toggle") is True
