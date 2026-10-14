"""Streaming sample-rate conversion and a jitter buffer for audio monitoring."""
import threading

import numpy as np


class Resampler:
    """Chunk-by-chunk resampler (anti-alias FIR + linear interpolation).

    Output is identical whether the signal is processed in one piece or in
    arbitrary chunks. For integer ratios (48 kHz -> 16 kHz) it is plain
    filtered decimation.
    """

    def __init__(self, sr_in, sr_out=16000, taps=63):
        self.sr_in, self.sr_out = int(sr_in), int(sr_out)
        self.ratio = self.sr_in / self.sr_out
        self.passthrough = self.sr_in == self.sr_out
        fc = 0.45 * min(self.sr_in, self.sr_out) / self.sr_in   # cycles/sample, a bit under Nyquist
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(taps)
        self._h = (h / h.sum()).astype(np.float32)
        self._hist = np.zeros(taps - 1, np.float32)
        self._last = np.float32(0.0)
        self._pos = 1.0  # next output position, in units of filtered samples (index 0 = previous chunk's last)

    def process(self, x):
        x = np.asarray(x, np.float32)
        if self.passthrough or x.size == 0:
            return x.copy()
        xx = np.concatenate([self._hist, x])
        self._hist = xx[-(len(self._h) - 1):]
        y = np.convolve(xx, self._h, mode="valid").astype(np.float32)
        yy = np.concatenate([[self._last], y])
        idx = np.arange(self._pos, len(yy) - 1, self.ratio)
        if idx.size:
            i0 = idx.astype(np.int64)
            frac = (idx - i0).astype(np.float32)
            out = yy[i0] * (1 - frac) + yy[i0 + 1] * frac
            self._pos = idx[-1] + self.ratio - (len(yy) - 1)
        else:
            out = np.zeros(0, np.float32)
            self._pos -= len(yy) - 1
        self._last = yy[-1]
        return out.astype(np.float32)


class Fifo:
    """Thread-safe ring buffer between an input and an output audio stream.

    Starts playing only after ``prefill`` frames are buffered, and trims the
    backlog back to ``prefill`` when clock drift lets it grow past ``max_fill``.
    """

    def __init__(self, capacity, channels, prefill, max_fill):
        self._buf = np.zeros((capacity, channels), np.float32)
        self._cap = capacity
        self._r = self._w = self._n = 0
        self._prefill = prefill
        self._max = max_fill
        self._waiting = True
        self._lock = threading.Lock()

    @property
    def level(self):
        return self._n

    def write(self, x):
        x = np.asarray(x, np.float32)
        with self._lock:
            m = len(x)
            if m >= self._cap:
                x, m = x[-self._cap:], self._cap
            over = self._n + m - self._cap
            if over > 0:
                self._drop(over)
            first = min(m, self._cap - self._w)
            self._buf[self._w:self._w + first] = x[:first]
            self._buf[:m - first] = x[first:]
            self._w = (self._w + m) % self._cap
            self._n += m
            if self._n > self._max:
                self._drop(self._n - self._prefill)

    def _drop(self, k):
        self._r = (self._r + k) % self._cap
        self._n -= k

    def read(self, m):
        out = np.zeros((m, self._buf.shape[1]), np.float32)
        with self._lock:
            if self._waiting:
                if self._n < self._prefill:
                    return out
                self._waiting = False
            k = min(m, self._n)
            first = min(k, self._cap - self._r)
            out[:first] = self._buf[self._r:self._r + first]
            out[first:k] = self._buf[:k - first]
            self._drop(k)
            if k < m:
                self._waiting = True  # underrun: rebuffer before playing again
        return out
