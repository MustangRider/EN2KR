"""Voice activity detection and utterance segmentation.

SileroVad runs the official Silero VAD ONNX model on onnxruntime (no torch).
Segmenter turns a continuous 16 kHz stream into utterances. All timing is
measured in samples, so processing hiccups cannot change where cuts happen.

v1 kept the last 22 *callback chunks* (~1.25 s with VB-Cable) as pre-roll and
prepended them to every utterance, which re-sent the tail of the previous
sentence to Whisper ("previous sentence words duplicated"). Here pre-roll is a
short ring of windows collected only while *not* speaking, so it can never
contain audio that already belonged to an earlier utterance.
"""
import collections
import logging

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
WINDOW = 512           # Silero window at 16 kHz (32 ms)
CONTEXT = 64           # samples of left context the v5+ model expects
TAIL_SEC = 0.2         # trailing silence kept after the last speech window


class SileroVad:
    def __init__(self, model_path):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        with open(model_path, "rb") as f:  # bytes: immune to non-ASCII paths
            self._session = ort.InferenceSession(f.read(), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), np.float32)
        self._context = np.zeros((1, CONTEXT), np.float32)

    def __call__(self, window):
        x = np.concatenate([self._context, window.reshape(1, WINDOW).astype(np.float32)], axis=1)
        prob, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -CONTEXT:]
        return float(prob[0][0])


class Segmenter:
    """Feed audio with ``feed``; finished utterances go to ``on_segment``.

    on_segment(audio: np.ndarray, info: dict) with info keys:
        start_sample, end_sample (absolute stream positions), forced (bool)
    on_speech(active: bool) reports speech start/stop for the UI lamp.
    """

    def __init__(self, vad, on_segment, on_speech=None, threshold=0.3, silence_sec=0.5,
                 preroll_sec=0.3, max_utterance_sec=12.0, min_speech_sec=0.25):
        self.vad = vad
        self.on_segment = on_segment
        self.on_speech = on_speech or (lambda active: None)
        self.configure(threshold, silence_sec, preroll_sec, max_utterance_sec, min_speech_sec)
        self._pending = np.zeros(0, np.float32)
        self._preroll = collections.deque(maxlen=self._preroll_windows)
        self._buf = []
        self._in_speech = False
        self._silence = 0           # consecutive non-speech samples
        self._speech = 0            # speech samples in current utterance
        self._last_speech_idx = -1  # index in _buf of last speech window
        self._pos = 0               # absolute sample position of next window
        self._start = 0

    def configure(self, threshold=None, silence_sec=None, preroll_sec=None,
                  max_utterance_sec=None, min_speech_sec=None):
        if threshold is not None:
            self.threshold = float(threshold)
            # Hysteresis as in Silero's reference code: harder to stop than to start.
            self.neg_threshold = max(0.01, self.threshold - 0.15)
        if silence_sec is not None:
            self._silence_limit = int(silence_sec * SAMPLE_RATE)
        if preroll_sec is not None:
            self._preroll_windows = max(0, int(round(preroll_sec * SAMPLE_RATE / WINDOW)))
            if hasattr(self, "_preroll"):
                self._preroll = collections.deque(self._preroll, maxlen=self._preroll_windows)
        if max_utterance_sec is not None:
            self._max_samples = int(max_utterance_sec * SAMPLE_RATE)
        if min_speech_sec is not None:
            self._min_speech = int(min_speech_sec * SAMPLE_RATE)

    @property
    def in_speech(self):
        return self._in_speech

    @property
    def position(self):
        """Absolute stream position (samples) processed so far."""
        return self._pos

    @property
    def utterance_start(self):
        return self._start

    def feed(self, chunk):
        if chunk.size == 0:
            return
        data = np.concatenate([self._pending, chunk.astype(np.float32, copy=False)])
        n = (len(data) // WINDOW) * WINDOW
        self._pending = data[n:]
        for i in range(0, n, WINDOW):
            self._process(data[i:i + WINDOW])

    def _process(self, win):
        prob = self.vad(win)
        pos = self._pos
        self._pos += WINDOW
        if not self._in_speech:
            if prob >= self.threshold:
                self._in_speech = True
                self._buf = list(self._preroll) + [win]
                self._start = pos - len(self._preroll) * WINDOW
                self._preroll.clear()
                self._silence = 0
                self._speech = WINDOW
                self._last_speech_idx = len(self._buf) - 1
                self.on_speech(True)
            elif self._preroll_windows:
                self._preroll.append(win)
            return

        self._buf.append(win)
        if prob >= self.neg_threshold:
            self._silence = 0
            self._speech += WINDOW
            self._last_speech_idx = len(self._buf) - 1
        else:
            self._silence += WINDOW
        if self._silence >= self._silence_limit:
            self._finish(forced=False)
        elif len(self._buf) * WINDOW >= self._max_samples:
            self._finish(forced=True)

    def _finish(self, forced):
        tail = int(TAIL_SEC * SAMPLE_RATE / WINDOW)
        keep = self._buf if forced else self._buf[:self._last_speech_idx + 1 + tail]
        speech = self._speech
        start = self._start
        self._buf = []
        self._speech = 0
        self._silence = 0
        if forced:
            # Still talking: the next window continues a fresh utterance.
            self._start = self._pos
            self._last_speech_idx = -1
        else:
            self._in_speech = False
            self.on_speech(False)
        if keep and speech >= self._min_speech:
            audio = np.concatenate(keep)
            self.on_segment(audio, {"start_sample": start, "end_sample": start + len(audio), "forced": forced})

    def flush(self):
        """Close an open utterance (stream stalled or stopped)."""
        if self._in_speech:
            self._finish(forced=False)
        self._pending = np.zeros(0, np.float32)

    def reset(self):
        self.flush()
        self._preroll.clear()
        self.vad.reset()
