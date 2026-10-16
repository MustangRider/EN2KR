"""Groq limits: client-side pacing, merged requests, limit reporting and the local stand-in."""
import threading
import time
from types import SimpleNamespace

import faster_whisper
import numpy as np
import pytest

from en2kr import stt
from en2kr.audio import SAMPLE_RATE
from en2kr.pipeline import Pipeline, PipelineEvents
from en2kr.settings import Settings

from .test_cloud_stt import FakeCloud, engine_with, seg


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pacer_never_exceeds_the_limit_in_any_minute():
    clock = Clock()
    pacer = stt.RequestPacer(20, clock=clock)
    sent = []
    while clock.t < 1000 + 600:           # someone talks non-stop for 10 minutes
        wait = pacer.wait_time()
        if wait <= 0:
            pacer.record()
            sent.append(clock.t)
        clock.t += 0.1
    assert sent[5] - sent[0] < 1          # a short exchange goes out without delay
    assert all(sum(1 for t in sent if s <= t < s + 60) <= 20 for s in sent)
    assert len(sent) >= 140               # ~14 per minute sustained after the burst


def test_pacer_spacing_after_burst():
    clock = Clock()
    pacer = stt.RequestPacer(20, clock=clock)
    for _ in range(pacer.burst):
        assert pacer.wait_time() == 0
        pacer.record()
    assert pacer.wait_time() == pytest.approx(60 / (20 - pacer.burst))


def test_pacing_is_off_unless_turned_on():
    e = engine_with(FakeCloud({"language": "English", "segments": [seg("Go.")]}))
    for _ in range(30):
        e.transcribe(np.zeros(16000, np.float32))
    assert e.pacer is None and e.cloud_wait() == 0
    e.pacer = stt.RequestPacer(20)
    for _ in range(e.pacer.burst):
        e.transcribe(np.zeros(16000, np.float32))
    assert e.cloud_wait() > 0


def test_limit_kind_and_wait_text():
    assert stt._limit_kind("Rate limit reached ... on requests per minute (RPM): Limit 20", 3) == "minute"
    assert stt._limit_kind("... on requests per day (RPD): Limit 2000", 50) == "day"
    assert stt._limit_kind("... on audio seconds per hour (ASH): Limit 7200", 900) == "hour"
    assert stt._limit_kind("", 30) == "minute" and stt._limit_kind("", 20000) == "day"
    assert stt.fmt_wait(42) == "42초" and stt.fmt_wait(61) == "2분" and stt.fmt_wait(7300) == "약 2시간"


def test_quota_reports_which_limit():
    e = engine_with(FakeCloud(error=stt.CloudQuotaError("한도", retry_after=40, kind="day")))
    e.transcribe(np.zeros(16000, np.float32))
    kind, until, fallback = e.limit_state()
    assert kind == "day" and 30 < until - time.monotonic() <= 40 and fallback == "off"
    assert "일일 한도" in e.describe()
    assert e.cloud_wait() == 0          # while limited, utterances go to the stand-in (or are skipped) at once


class FakeModel:
    def __init__(self, path, device, compute_type, cpu_threads):
        self.path = path

    def transcribe(self, audio, language=None, **opts):
        s = SimpleNamespace(text="Enemy on the left.", avg_logprob=-0.1, no_speech_prob=0.0)
        return iter([s]), SimpleNamespace(language="en", all_language_probs=[("en", 0.99)])

    def detect_language(self, audio):
        return "en", 0.99, [("en", 0.99), ("ko", 0.01)]


@pytest.fixture
def fake_local(monkeypatch):
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(stt, "download", lambda model_id, progress=None: f"path:{model_id}")
    monkeypatch.setattr(stt.gpu, "cuda_device_count", lambda: 1)


def _join_fallback_threads():
    for t in threading.enumerate():
        if t.name in ("stt-fallback", "stt-gate"):
            t.join(5)


def test_local_stand_in_takes_over_during_a_limit(fake_local):
    notes = []
    cloud = FakeCloud(error=stt.CloudQuotaError("한도", retry_after=60))
    e = engine_with(cloud)
    e.cfg["cloud"] = {"fallback_model": "distil-small.en"}
    e._fb_state, e._notice = "idle", notes.append
    first = e.transcribe(np.zeros(16000, np.float32))
    assert first.skipped == "cloud_quota" and "준비 중" in first.skip_note
    _join_fallback_threads()
    assert e.limit_state()[2] == "ready"
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.text == "Enemy on the left." and r.via == "fallback"
    assert len(cloud.calls) == 1            # Groq is not asked again until the limit is over
    assert e._fb.model_id == "distil-small.en" and e._fb.cfg["mode"] == "english"


def test_multilingual_stand_in_for_english_mode_still_checks_korean():
    e = engine_with(FakeCloud(), mode="english")
    e.cfg["cloud"] = {"fallback_model": "small"}
    cfg = e._fallback_cfg(e.cfg)
    assert cfg["mode"] == "fixed" and cfg["language"] == "en" and cfg["skip_korean"] is True


def test_stand_in_is_unloaded_once_groq_works_again():
    notes = []
    e = engine_with(FakeCloud({"language": "English", "segments": [seg("Go.")]}))
    e._fb, e._fb_state, e._notice = object(), "ready", notes.append
    e._fb_used = time.monotonic() - stt.FALLBACK_IDLE_SEC - 1
    e.transcribe(np.zeros(16000, np.float32))
    assert e._fb is None and e._fb_state == "idle" and notes and "VRAM" in notes[0]


# ---------- pipeline ----------
def _pipeline(tmp_path, events=None):
    p = Pipeline(Settings(str(tmp_path / "s.json")).load(), events)
    p._running = True
    return p


def _utt(sec, start):
    return np.full(int(sec * SAMPLE_RATE), 0.1, np.float32), {"start_sample": int(start * SAMPLE_RATE),
                                                               "end_sample": int((start + sec) * SAMPLE_RATE)}


def test_pipeline_keeps_what_is_said_while_paced(tmp_path):
    p = _pipeline(tmp_path)
    for i in range(3):
        p._seg_q.put(_utt(1.0, 2 + i * 3))
    p._wait_for_slot(0.3)
    assert len(p._backlog) == 3 and p._seg_q.empty()


def test_only_continuations_share_a_request(tmp_path):
    p = _pipeline(tmp_path)
    first = _utt(1.0, 0)
    first[1]["queued"] = time.monotonic()
    p._backlog.extend([_utt(1.0, 1.5), _utt(1.0, 3.0), _utt(1.0, 6.0)])   # gaps 0.5 s, 0.5 s, 2 s
    audio, info = p._join_continuations(first)
    assert info["start_sample"] == 0 and info["end_sample"] == 4 * SAMPLE_RATE
    assert len(audio) == 4 * SAMPLE_RATE              # the 0.5 s pauses are kept as silence
    assert len(p._backlog) == 1                       # the utterance after a 2 s pause waits for its own request


def test_utterances_that_waited_too_long_are_dropped(tmp_path):
    notes = []
    p = _pipeline(tmp_path, PipelineEvents(notice=notes.append))
    old = _utt(1.0, 0)
    old[1]["queued"] = time.monotonic() - 20
    fresh = _utt(1.0, 30)
    fresh[1]["queued"] = time.monotonic()
    p._backlog.append(fresh)
    audio, info = p._join_continuations(old)
    assert info["start_sample"] == 30 * SAMPLE_RATE and notes and "1개" in notes[0]


def test_pipeline_reports_the_limit_once_and_its_end(tmp_path):
    shown = []
    p = _pipeline(tmp_path, PipelineEvents(limit=lambda m, s: shown.append((m, round(s)))))
    p.engine = engine_with(FakeCloud(error=stt.CloudQuotaError("한도", retry_after=30)))
    p._handle_segment(*_utt(1.0, 0))
    p._handle_segment(*_utt(1.0, 2))
    assert len(shown) == 1 and "분당 요청 한도" in shown[0][0] and 28 <= shown[0][1] <= 30
    p.engine.limit = ("minute", time.monotonic() - 1)
    p._report_stt_ready()
    assert shown[-1] == ("", 0)
