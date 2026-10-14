import json
import os
import wave

import numpy as np
import pytest

from en2kr import stt
from en2kr.settings import Settings

from .conftest import ASSETS


def test_hangul_ratio():
    assert stt.hangul_ratio("왼쪽에 적 레이더 있어요.") == 1.0
    assert stt.hangul_ratio("Enemy Raider on the left.") == 0.0
    assert stt.hangul_ratio("오 나이스 샷 nice") > 0.5   # mostly Korean with an English word
    assert stt.hangul_ratio("...") == 0.0


def test_retry_after_parsing():
    class R:
        def __init__(self, headers):
            self.headers = headers
    assert stt._retry_after(R({"retry-after": "12"})) == 12
    assert stt._retry_after(R({"x-ratelimit-reset-requests": "6m28.8s"})) == pytest.approx(388.8)
    assert stt._retry_after(R({}), default=30) == 30


class FakeCloud:
    remaining = 1500
    model = "whisper-large-v3"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def transcribe(self, audio, language=None, prompt=None):
        self.calls.append((language, prompt))
        if self.error:
            raise self.error
        return self.reply


def engine_with(cloud, **cfg):
    e = stt.SttEngine()
    e.cloud, e.device, e.model_id = cloud, "cloud", cloud.model
    e.cfg = {"engine": "cloud", "skip_korean": True, "use_context": True, **cfg}
    return e


def seg(text, lp=-0.2, ns=0.0):
    return {"text": text, "avg_logprob": lp, "no_speech_prob": ns}


def test_cloud_keeps_english_even_if_labelled_korean():
    e = engine_with(FakeCloud({"language": "Korean", "segments": [seg("Enemy Raider on the left.")]}))
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.skipped is None and r.text == "Enemy Raider on the left." and r.language == "en"


def test_cloud_skips_korean_script():
    e = engine_with(FakeCloud({"language": "Korean", "segments": [seg("블루 게이트 뒤쪽으로 가세요.")]}))
    r = e.transcribe(np.zeros(16000, np.float32))
    assert r.skipped == "korean" and r.text == ""


def test_cloud_quota_pauses_then_skips_without_calling():
    cloud = FakeCloud(error=stt.CloudQuotaError("한도", retry_after=60))
    e = engine_with(cloud)
    r1 = e.transcribe(np.zeros(16000, np.float32))
    r2 = e.transcribe(np.zeros(16000, np.float32))
    assert r1.skipped == "cloud_quota" and r2.skipped == "cloud_paused"
    assert len(cloud.calls) == 1


def test_cloud_sends_context_and_auto_language():
    cloud = FakeCloud({"language": "English", "segments": [seg("Push now.")]})
    e = engine_with(cloud)
    e.transcribe(np.zeros(16000, np.float32), prev_text="They are behind the wall")
    assert cloud.calls == [(None, "They are behind the wall")]


def test_cloud_key_encrypted_on_disk(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(str(p)).load()
    s.set("stt.cloud.key", "gsk_test_secret_value")
    s.save()
    raw = p.read_text(encoding="utf-8")
    assert "gsk_test_secret_value" not in raw
    assert json.loads(raw)["stt"]["cloud"]["key"].startswith("dpapi:")
    assert Settings(str(p)).load().get("stt.cloud.key") == "gsk_test_secret_value"


@pytest.mark.skipif(not os.environ.get("EN2KR_GROQ_KEY"), reason="set EN2KR_GROQ_KEY to run against Groq")
def test_live_groq_english_and_korean():
    eng = stt.SttEngine()
    eng.load({"engine": "cloud", "skip_korean": True,
              "cloud": {"model": "whisper-large-v3", "key": os.environ["EN2KR_GROQ_KEY"]}})

    def wav(name):
        with wave.open(os.path.join(ASSETS, name)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

    r = eng.transcribe(wav("speech_en.wav"))
    assert "friendly" in r.text.lower() and r.language == "en"
    assert eng.transcribe(wav("speech_ko.wav")).skipped == "korean"
