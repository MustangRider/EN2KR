"""The Korean detector next to English-only models is optional: it must never block or break recognition."""
import threading
from types import SimpleNamespace

import faster_whisper
import numpy as np
import pytest

from en2kr import stt

CFG = {"model": "distil-large-v3.5", "mode": "english", "device": "auto", "skip_korean": True}


class FakeModel:
    gate_error = None        # raised by the detector's __init__
    detect_error_after = None  # detector's detect_language raises after this many calls

    def __init__(self, path, device, compute_type, cpu_threads):
        self.path, self.device, self.calls = path, device, 0
        if path == "path:tiny" and FakeModel.gate_error:
            raise FakeModel.gate_error

    def transcribe(self, audio, language=None, **opts):
        seg = SimpleNamespace(text="Enemy on the left.", avg_logprob=-0.1, no_speech_prob=0.0)
        return iter([seg]), SimpleNamespace(language="en", all_language_probs=[("en", 0.99)])

    def detect_language(self, audio):
        self.calls += 1
        if FakeModel.detect_error_after is not None and self.calls > FakeModel.detect_error_after:
            raise RuntimeError("CUDA failed")
        return "en", 0.99, [("en", 0.99), ("ko", 0.01)]


@pytest.fixture
def fake(monkeypatch):
    FakeModel.gate_error = FakeModel.detect_error_after = None
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(stt, "download", lambda model_id, progress=None: f"path:{model_id}")
    monkeypatch.setattr(stt.gpu, "cuda_device_count", lambda: 1)
    return monkeypatch


def test_detector_loads_in_background_and_turns_on(fake):
    e = stt.SttEngine()
    e.load(CFG)
    assert e.ready
    assert e.wait_gate(5) and e.gate_state == "on" and e.gate.path == "path:tiny"


def test_no_gpu_means_no_detector(fake):
    fake.setattr(stt.gpu, "cuda_device_count", lambda: 0)
    e = stt.SttEngine()
    warnings = e.load(CFG)
    assert e.device == "cpu" and e.gate is None and e.gate_state == "cpu"
    assert e.wait_gate(0) and any("GPU(CUDA)" in w for w in warnings)
    assert e.transcribe(np.zeros(16000, np.float32)).text == "Enemy on the left."


def test_detector_load_failure_turns_it_off(fake):
    FakeModel.gate_error = OSError("download blocked")
    notes = []
    e = stt.SttEngine()
    e.load(CFG, notice=notes.append)
    assert e.wait_gate(5) and e.gate_state == "failed" and e.gate is None
    assert len(notes) == 1 and "download blocked" in notes[0]
    assert e.transcribe(np.zeros(16000, np.float32)).text == "Enemy on the left."


def test_detector_runtime_failure_turns_it_off_and_keeps_recognising(fake):
    FakeModel.detect_error_after = 1  # warm-up call succeeds, the first real one fails
    notes, changes = [], []
    e = stt.SttEngine()
    e.load(CFG, notice=notes.append, changed=lambda: changes.append(e.gate_state))
    assert e.wait_gate(5) and e.gate_state == "on"
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.text == "Enemy on the left." and e.gate_state == "failed" and e.gate is None
    assert len(notes) == 1 and changes == ["on", "failed"]
    e.transcribe(np.zeros(16000, np.float32))
    assert len(notes) == 1  # reported once


def test_stuck_detector_download_does_not_block_recognition(fake):
    release = threading.Event()

    def slow_download(model_id, progress=None):
        if model_id == "tiny":
            release.wait(10)
        return f"path:{model_id}"

    fake.setattr(stt, "download", slow_download)
    e = stt.SttEngine()
    e.load(CFG)
    assert e.ready and e.gate_state == "loading" and "준비 중" in e.describe()
    assert e.transcribe(np.zeros(16000, np.float32)).text == "Enemy on the left."
    release.set()
    assert e.wait_gate(5) and e.gate_state == "on"


def test_switching_to_cloud_drops_a_detector_still_loading(fake):
    release = threading.Event()
    fake.setattr(stt, "download", lambda m, progress=None: (m == "tiny" and release.wait(10), f"path:{m}")[1])
    e = stt.SttEngine()
    e.load(CFG)
    assert e.gate_state == "loading"

    class Cloud:
        model, remaining = "whisper-large-v3", None

        def __init__(self, key, model):
            pass

        def check(self):
            pass

    fake.setattr(stt, "GroqStt", Cloud)
    e.load({"engine": "cloud", "cloud": {"key": "k", "model": "whisper-large-v3"}})
    release.set()
    for t in threading.enumerate():
        if t.name == "stt-gate":
            t.join(5)
    assert e.gate is None and e.gate_state == "off" and e.device == "cloud"
