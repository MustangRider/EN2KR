"""Toggle chimes: a soft two-note rise (on) and fall (off).

Synthesised with numpy (a sine with a little 2nd/3rd harmonic and a quickly
fading glassy partial, short raised-cosine attack, exponential decay and two
faint echoes), written once per volume into the app folder and played with
winsound asynchronously, so nothing is bundled and the UI never waits.
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
NOTE_GAP = 0.075            # second note starts this long after the first
E5, B5 = 659.25, 987.77     # a perfect fifth: open and clean, neither alarm nor error-like
PEAK = 0.35                 # full scale at volume 100 (about -9 dBFS); the default 50 is quieter still


def _note(freq, length, decay):
    t = np.arange(int(length * SAMPLE_RATE)) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(2 * np.pi * 2 * freq * t)
            + 0.06 * np.sin(2 * np.pi * 3 * freq * t))
    shimmer = 0.08 * np.sin(2 * np.pi * 2.76 * freq * t) * np.exp(-t / 0.03)
    attack = np.minimum(1.0, t / 0.004)
    attack = 0.5 - 0.5 * np.cos(np.pi * attack)
    return (tone * np.exp(-t / decay) + shimmer) * attack


def chime(rising, volume=0.5):
    """Mono float32 samples of the on (rising) or off (falling) chime."""
    first, second = (E5, B5) if rising else (B5, E5)
    total = 0.48
    out = np.zeros(int(total * SAMPLE_RATE))
    a = _note(first, total, 0.10)
    b = _note(second, total - NOTE_GAP, 0.15 if rising else 0.12)
    out += 0.8 * a
    start = int(NOTE_GAP * SAMPLE_RATE)
    out[start:start + len(b)] += b
    for delay, gain in ((0.045, 0.18), (0.09, 0.08)):   # a little room, so it doesn't sound like a beep
        d = int(delay * SAMPLE_RATE)
        out[d:] += gain * out[:-d].copy()
    fade = int(0.03 * SAMPLE_RATE)
    out[-fade:] *= np.linspace(1.0, 0.0, fade)
    out *= PEAK * min(max(volume, 0.0), 1.0) / max(1e-9, float(np.max(np.abs(out))))
    return out.astype(np.float32)


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
    path = os.path.join(folder, f"toggle_{'on' if rising else 'off'}_{volume}.wav")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(wav_bytes(chime(rising, volume / 100)))
        os.replace(tmp, path)
    return path


def play_toggle(on, volume=50):
    """Play the on/off chime without blocking. Failures only go to the log."""
    try:
        import winsound
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        winsound.PlaySound(chime_path(on, volume), flags)
    except Exception as e:
        log.warning("Toggle sound failed: %s", e)
