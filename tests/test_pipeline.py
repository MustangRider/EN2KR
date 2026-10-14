import os

import numpy as np
import pytest

from en2kr import selftest, stt
from en2kr.settings import Settings

from .conftest import ASSETS

pytestmark = pytest.mark.skipif(not stt.is_downloaded("small"), reason="model not downloaded")


def _settings(tmp_path):
    s = Settings(str(tmp_path / "s.json")).load()
    s.set("stt.model", "small")
    s.set("stt.mode", "auto")
    s.set("stt.auto_languages", ["en", "ko"])
    s.set("vad.threshold", 0.5)
    return s


def test_pipeline_end_to_end_english_then_korean(tmp_path):
    en = selftest.load_wav(os.path.join(ASSETS, "speech_en.wav"))
    ko = selftest.load_wav(os.path.join(ASSETS, "speech_ko.wav"))
    wav = tmp_path / "mix.wav"
    import wave
    mix = np.concatenate([en, np.zeros(16000, np.float32), ko])
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())

    r = selftest.run(_settings(tmp_path), str(wav))
    report = selftest.format_report(r)
    assert r["ok"], report
    sent = [l for l in r["lines"] if l.line_id is not None]
    skipped = [l for l in r["lines"] if l.note == "한국어 음성 건너뜀"]
    assert sent and all(l.language == "en" for l in sent), report
    assert skipped, report
    text = " ".join(l.raw for l in sent).lower()
    assert "friendly" in text and "together" in text, report
    # no words repeated across consecutive utterances (v1 preroll bug)
    for a, b in zip(sent, sent[1:]):
        assert a.raw.split()[-1].lower().strip(".,!?") != b.raw.split()[0].lower().strip(".,!?"), report
    # each line is translated exactly once, even when fragments were merged
    ids = [t.line_id for t in r["translations"]]
    assert sorted(ids) == sorted({l.line_id for l in sent}), report
    # no provider configured -> translation reports it instead of crashing
    assert r["translations"] and all("설정" in (t.error or "") for t in r["translations"]), report
