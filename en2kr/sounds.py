"""Toggle beeps: two short plain beeps, low-high (on) or high-low (off).

Pure sine tones with 4 ms raised-cosine edges (no click), synthesised with numpy,
written once per volume into the app folder and played with winsound
asynchronously, so nothing is bundled and the UI never waits.
The app's own sound is not in the capture: only the chosen program is.
"""
import io
import logging
import os
import wave

import numpy as np

from . import paths

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
LOW, HIGH = 880.0, 1318.5   # A5 and E6, a fifth apart
BEEP_SEC = 0.07
GAP_SEC = 0.04
EDGE_SEC = 0.004
PEAK = 0.35                 # full scale at volume 100 (about -9 dBFS); the default 50 is quieter still


def _beep(freq):
    t = np.arange(int(BEEP_SEC * SAMPLE_RATE)) / SAMPLE_RATE
    edge = np.clip(np.minimum(t, BEEP_SEC - t) / EDGE_SEC, 0, 1)
    return np.sin(2 * np.pi * freq * t) * (0.5 - 0.5 * np.cos(np.pi * edge))


def chime(rising, volume=0.5):
    """Mono float32 samples of the on (rising) or off (falling) beeps."""
    first, second = (LOW, HIGH) if rising else (HIGH, LOW)
    out = np.concatenate([_beep(first), np.zeros(int(GAP_SEC * SAMPLE_RATE)), _beep(second)])
    return (out * PEAK * min(max(volume, 0.0), 1.0)).astype(np.float32)


def wav_bytes(samples):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def chime_path(rising, volume):
    volume = int(min(max(volume, 0), 100))
    folder = os.path.join(paths.app_dir(), "sounds")
    path = os.path.join(folder, f"beep_{'on' if rising else 'off'}_{volume}.wav")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(wav_bytes(chime(rising, volume / 100)))
        os.replace(tmp, path)
    return path


def play_toggle(on, volume=50):
    """Play the on/off beeps without blocking. Failures only go to the log."""
    try:
        import winsound
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        winsound.PlaySound(chime_path(on, volume), flags)
    except Exception as e:
        log.warning("Toggle sound failed: %s", e)
