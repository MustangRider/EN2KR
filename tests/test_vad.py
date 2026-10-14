import os
import wave

import numpy as np
import pytest

from en2kr import paths
from en2kr.vad import WINDOW, Segmenter, SileroVad

from .conftest import ASSETS

SR = 16000


class EnergyVad:
    """Deterministic stand-in for Silero: 'speech' when the window is loud."""

    def __call__(self, win):
        return 0.9 if np.abs(win).max() > 0.05 else 0.0

    def reset(self):
        pass


def tone(sec, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def silence(sec):
    return np.zeros(int(sec * SR), np.float32)


def run(segmenter, audio, chunk=1000):
    for i in range(0, len(audio), chunk):
        segmenter.feed(audio[i:i + chunk])


def collect(**kw):
    segs = []
    seg = Segmenter(EnergyVad(), lambda a, info: segs.append((a, info)), **kw)
    return seg, segs


def test_consecutive_utterances_do_not_overlap():
    """Regression for v1 bug: preroll re-sent the previous sentence's tail."""
    seg, segs = collect(silence_sec=0.2, preroll_sec=0.3)
    audio = np.concatenate([silence(0.5), tone(1.0), silence(0.25), tone(1.0), silence(1.0)])
    run(seg, audio)
    assert len(segs) == 2
    first_end = segs[0][1]["end_sample"]
    second_start = segs[1][1]["start_sample"]
    assert second_start >= first_end, (first_end, second_start)


def test_preroll_is_bounded_and_trailing_silence_trimmed():
    seg, segs = collect(silence_sec=0.5, preroll_sec=0.3)
    run(seg, np.concatenate([silence(2.0), tone(1.0), silence(1.0)]))
    (a, info), = segs
    # speech 1.0 s + preroll <= 0.3 s + tail 0.2 s (window rounding allowed)
    assert len(a) / SR <= 1.0 + 0.3 + 0.2 + 2 * WINDOW / SR
    assert info["start_sample"] >= int((2.0 - 0.3) * SR) - WINDOW


def test_long_speech_is_cut_at_max_length():
    seg, segs = collect(max_utterance_sec=3.0)
    run(seg, np.concatenate([tone(7.5), silence(1.0)]))
    assert [s[1]["forced"] for s in segs] == [True, True, False]
    assert all(len(a) <= 3.0 * SR + WINDOW for a, _ in segs)
    # forced cuts are contiguous: no audio lost or duplicated
    for (_, prev), (_, nxt) in zip(segs, segs[1:]):
        assert nxt["start_sample"] == prev["end_sample"]


def test_short_blips_are_dropped():
    seg, segs = collect(min_speech_sec=0.25)
    run(seg, np.concatenate([silence(0.5), tone(0.064), silence(1.0)]))
    assert segs == []


def test_flush_closes_open_utterance():
    seg, segs = collect()
    run(seg, np.concatenate([silence(0.3), tone(1.0)]))
    assert segs == [] and seg.in_speech
    seg.flush()
    assert len(segs) == 1 and not seg.in_speech


def test_silero_onnx_detects_speech():
    path = paths.VAD_MODEL_PATH
    if not os.path.exists(path):
        pytest.skip("VAD model not present")
    with wave.open(os.path.join(ASSETS, "speech_en.wav")) as w:
        speech = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    segs = []
    seg = Segmenter(SileroVad(path), lambda a, info: segs.append(info), threshold=0.5, silence_sec=0.5)
    run(seg, np.concatenate([silence(1.0), speech, silence(1.5)]))
    assert 1 <= len(segs) <= 4
    total = sum(i["end_sample"] - i["start_sample"] for i in segs) / SR
    assert 0.6 * len(speech) / SR < total < 1.3 * len(speech) / SR
