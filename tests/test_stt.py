import os
import wave

import numpy as np
import pytest

from en2kr import stt

from .conftest import ASSETS


def test_choose_language_restricted_to_allowed():
    probs = [("ja", 0.5), ("en", 0.3), ("de", 0.2)]
    assert stt.choose_language(probs, ["en", "de"])[0] == "en"


def test_choose_language_falls_back_when_unsure():
    probs = [("en", 0.4), ("de", 0.35), ("ko", 0.25)]
    lang, conf = stt.choose_language(probs, ["en", "de", "ko"], fallback="en", min_confidence=0.6)
    assert lang == "en" and conf < 0.6


def test_choose_language_confident_korean():
    lang, conf = stt.choose_language([("ko", 0.9), ("en", 0.05)], ["en", "ko"])
    assert lang == "ko" and conf > 0.9


def test_resolve_auto_model():
    assert stt.resolve_model_id({"model": "auto", "mode": "english"}, cuda=True) == "distil-large-v3.5"
    assert stt.resolve_model_id({"model": "auto", "mode": "english"}, cuda=False) == "distil-small.en"
    assert stt.resolve_model_id({"model": "auto", "mode": "auto"}, cuda=True) == "large-v3-turbo"
    assert stt.resolve_model_id({"model": "small", "mode": "auto"}, cuda=True) == "small"


def test_catalog_stats_loaded():
    stt.load_stats()
    m = stt.model_info("distil-large-v3.5")
    if m.vram_mb is None:
        pytest.skip("no model_stats.json")
    assert m.vram_mb > 0 and m.rtf > 0


def _wav(name):
    with wave.open(os.path.join(ASSETS, name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@pytest.fixture(scope="module")
def auto_engine():
    if not stt.is_downloaded("small"):
        pytest.skip("model not downloaded")
    eng = stt.SttEngine()
    eng.load({"model": "small", "mode": "auto", "device": "auto", "auto_languages": ["en", "ko"],
              "skip_korean": True})
    return eng


def test_auto_mode_transcribes_english(auto_engine):
    r = auto_engine.transcribe(_wav("speech_en.wav"))
    assert r.language == "en" and r.skipped is None
    assert "friendly" in r.text.lower()


def test_auto_mode_skips_korean(auto_engine):
    r = auto_engine.transcribe(_wav("speech_ko.wav"))
    assert r.language == "ko" and r.skipped == "korean" and r.text == ""


def test_auto_mode_keeps_korean_accented_english(auto_engine):
    """Whisper scores Korean-accented English as 'ko' (~0.75); it must not be dropped."""
    r = auto_engine.transcribe(_wav("speech_en_accented.wav"))
    assert r.skipped is None and r.language == "en"
    assert "together" in r.text.lower()


def test_fixed_english_mode_ignores_detection(auto_engine):
    auto_engine.update_options({**auto_engine.cfg, "mode": "fixed", "language": "en"})
    try:
        r = auto_engine.transcribe(_wav("speech_en.wav"))
        assert r.language == "en" and "friendly" in r.text.lower()
    finally:
        auto_engine.update_options({**auto_engine.cfg, "mode": "auto"})


@pytest.fixture(scope="module")
def english_engine():
    if not (stt.is_downloaded("distil-large-v3.5") and stt.is_downloaded("tiny")):
        pytest.skip("models not downloaded")
    eng = stt.SttEngine()
    eng.load({"model": "distil-large-v3.5", "mode": "english", "device": "auto", "skip_korean": True})
    assert eng.gate is not None
    return eng


def test_english_only_skips_korean_instead_of_translating_it(english_engine):
    """English-only models turn Korean speech into English text; the detector must drop it."""
    r = english_engine.transcribe(_wav("speech_ko.wav"))
    assert r.skipped == "korean" and r.text == ""


def test_english_only_keeps_english_and_accented_english(english_engine):
    assert "friendly" in english_engine.transcribe(_wav("speech_en.wav")).text.lower()
    r = english_engine.transcribe(_wav("speech_en_accented.wav"))
    assert r.skipped is None and "together" in r.text.lower()
