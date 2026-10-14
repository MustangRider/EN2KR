import numpy as np

from en2kr.audio.resample import Fifo, Resampler


def tone(freq, sr, sec=1.0, amp=0.5):
    t = np.arange(int(sr * sec)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def test_48k_to_16k_keeps_speech_band_and_removes_aliases():
    r = Resampler(48000)
    voice = r.process(tone(1000, 48000))
    assert abs(len(voice) - 16000) <= 1
    assert abs(rms(voice[200:]) - 0.5 / np.sqrt(2)) < 0.02
    alias = Resampler(48000).process(tone(12000, 48000))  # would fold to 4 kHz without filtering
    assert rms(alias[200:]) < 0.01


def test_chunked_equals_one_shot():
    x = tone(700, 44100, 0.5) + tone(2300, 44100, 0.5, 0.2)
    whole = Resampler(44100).process(x)
    r = Resampler(44100)
    rng = np.random.default_rng(1)
    parts, i = [], 0
    while i < len(x):
        n = int(rng.integers(1, 900))
        parts.append(r.process(x[i:i + n]))
        i += n
    chunked = np.concatenate(parts)
    assert len(chunked) == len(whole)
    assert np.allclose(chunked, whole, atol=1e-5)


def test_fifo_prefill_underrun_and_drift_trim():
    f = Fifo(capacity=1000, channels=2, prefill=100, max_fill=300)
    f.write(np.ones((50, 2)))
    assert not f.read(10).any()            # still prefilling
    f.write(np.ones((60, 2)))
    assert f.read(10).all()                # 110 >= prefill -> plays
    out = f.read(200)                      # only 100 left -> tail is silence, rebuffer
    assert out[:100].all() and not out[100:].any()
    f.write(np.ones((400, 2)))             # > max_fill -> trimmed back to prefill
    assert f.level == 100


def test_vbcable_endpoint_names():
    from en2kr.audio.vbcable import is_cable_endpoint
    assert is_cable_endpoint("CABLE Input(VB-Audio Virtual Cable)")
    assert is_cable_endpoint("스피커(VB-Audio Virtual Cable)")        # name right after install, before reboot
    assert is_cable_endpoint("CABLE Output(VB-Audio Virtual Cable)")
    assert not is_cable_endpoint("CABLE In 16 Ch(VB-Audio Virtual Cable)")
    assert not is_cable_endpoint("CABLE In 16ch(VB-Audio Virtual Cable)")
    assert not is_cable_endpoint("스피커(D30)")
