"""Capture from an input device (advanced fallback, e.g. VB-Cable "CABLE Output").

Unlike v1 this never picks a device on its own: if the configured device is
missing it fails visibly instead of silently falling back to a microphone.
"""
import logging
import threading

import numpy as np

from . import SAMPLE_RATE

log = logging.getLogger(__name__)

# Host APIs that resample to 16 kHz for us; WASAPI shared mode would reject it.
_PREFERRED_APIS = ("Windows DirectSound", "MME")


def device_key(name, hostapi):
    return f"{name} | {hostapi}"


def list_input_devices():
    import sounddevice as sd
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        api = apis[d["hostapi"]]["name"]
        if d["max_input_channels"] > 0 and api in _PREFERRED_APIS:
            out.append({"index": i, "name": d["name"], "hostapi": api, "key": device_key(d["name"], api)})
    out.sort(key=lambda d: (_PREFERRED_APIS.index(d["hostapi"]), "cable" not in d["name"].lower(), d["name"]))
    return out


def find_device(key):
    for d in list_input_devices():
        if d["key"] == key:
            return d
    return None


class DeviceCapture:
    def __init__(self, key, on_audio, on_end=None):
        self.key = key
        self.on_audio = on_audio
        self.on_end = on_end
        self.error = None
        self._stream = None
        self._ended = threading.Event()

    @property
    def description(self):
        return self.key

    @property
    def running(self):
        return self._stream is not None and self._stream.active

    def start(self, wait=None):
        import sounddevice as sd
        dev = find_device(self.key)
        if dev is None:
            raise RuntimeError(f"오디오 장치를 찾을 수 없습니다: {self.key}")

        def callback(indata, frames, time_info, status):
            self.on_audio(np.ascontiguousarray(indata[:, 0], dtype=np.float32).copy())

        self._stream = sd.InputStream(device=dev["index"], channels=1, samplerate=SAMPLE_RATE,
                                      dtype="float32", blocksize=512, callback=callback,
                                      finished_callback=lambda: self._finish("stopped"))
        self._stream.start()
        log.info("Device capture started: %s", self.key)
        return self

    def _finish(self, reason):
        if not self._ended.is_set():
            self._ended.set()
            if self.on_end:
                self.on_end(reason)

    def stop(self, timeout=None):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:  # pragma: no cover
                log.warning("Closing input stream failed: %s", e)
        self._finish("stopped")
