"""Voice chat only, via VB-CABLE.

The game's voice chat output device is set to "CABLE Input (VB-Audio Virtual
Cable)", so voice chat (and nothing else) arrives on "CABLE Output". We record
that at the device rate, feed a 16 kHz mono copy to the pipeline and, because
the player no longer hears voice chat directly, play it back to their headset.
"""
import logging

import numpy as np

from . import SAMPLE_RATE
from .resample import Fifo, Resampler

log = logging.getLogger(__name__)

MONITOR_PREFILL_SEC = 0.03
MONITOR_MAX_SEC = 0.15


def is_cable_name(name):
    n = name.lower()
    return "vb-audio virtual cable" in n or n.startswith("cable ")


def _wasapi_index(sd):
    for i, api in enumerate(sd.query_hostapis()):
        if "wasapi" in api["name"].lower():
            return i
    return None


def _find(sd, hostapi, want_input, match):
    for i, d in enumerate(sd.query_devices()):
        chans = d["max_input_channels"] if want_input else d["max_output_channels"]
        if d["hostapi"] == hostapi and chans > 0 and match(d["name"]):
            return i, d
    return None, None


class VoiceChatCapture:
    def __init__(self, on_audio, on_end=None, monitor=True, monitor_device="", volume=1.0):
        self.on_audio = on_audio
        self.on_end = on_end
        self.monitor = monitor
        self.monitor_device = monitor_device
        self.volume = float(volume)
        self.error = None
        self._in = self._out = None
        self._fifo = None
        self._resampler = None
        self._channels = 1
        self._out_name = ""
        self._ended = False

    @property
    def description(self):
        if self._out is not None:
            return f"VB-CABLE 음성 채팅 · 듣기: {self._out_name}"
        return "VB-CABLE 음성 채팅 · 듣기 꺼짐"

    @property
    def running(self):
        return self._in is not None and self._in.active

    def start(self, wait=None):
        import sounddevice as sd
        # PortAudio caches the device list; refresh so a freshly installed VB-CABLE shows up.
        sd._terminate()
        sd._initialize()
        api = _wasapi_index(sd)
        if api is None:
            raise RuntimeError("WASAPI 오디오를 사용할 수 없습니다")
        in_idx, in_dev = _find(sd, api, True,
                               lambda n: "cable output" in n.lower() and "vb-audio virtual cable" in n.lower())
        if in_idx is None:
            raise RuntimeError("VB-CABLE(CABLE Output)을 찾을 수 없습니다. '음성 채팅 설정'에서 VB-CABLE을 설치하세요.")
        sr = int(in_dev["default_samplerate"])
        self._channels = min(2, in_dev["max_input_channels"])
        self._resampler = Resampler(sr, SAMPLE_RATE)
        wasapi = sd.WasapiSettings(auto_convert=True)

        if self.monitor:
            if self.monitor_device:
                out_idx, out_dev = _find(sd, api, False, lambda n: n == self.monitor_device)
                if out_idx is None:
                    raise RuntimeError(f"음성 채팅을 들려줄 장치를 찾을 수 없습니다: {self.monitor_device}")
            else:
                out_idx = sd.query_hostapis(api)["default_output_device"]
                out_dev = sd.query_devices(out_idx)
            if is_cable_name(out_dev["name"]):
                raise RuntimeError("Windows 기본 출력 장치가 VB-CABLE로 되어 있습니다. 헤드셋을 기본 장치로 되돌리거나 "
                                   "'음성 채팅 설정'에서 들려줄 장치를 직접 고르세요.")
            self._out_name = out_dev["name"]
            self._fifo = Fifo(capacity=sr, channels=2, prefill=int(sr * MONITOR_PREFILL_SEC),
                              max_fill=int(sr * MONITOR_MAX_SEC))
            self._out = sd.OutputStream(device=out_idx, samplerate=sr, channels=2, dtype="float32",
                                        latency="low", extra_settings=wasapi, callback=self._out_cb)
        self._in = sd.InputStream(device=in_idx, samplerate=sr, channels=self._channels, dtype="float32",
                                  latency="low", extra_settings=wasapi, callback=self._in_cb,
                                  finished_callback=lambda: self._finish("device stopped"))
        try:
            if self._out is not None:
                self._out.start()
            self._in.start()
        except Exception:
            self._close()
            raise
        log.info("Voice chat capture started (%d Hz, %d ch, monitor=%s)", sr, self._channels, self._out_name or "off")
        return self

    def _in_cb(self, indata, frames, time_info, status):
        mono = indata.mean(axis=1) if self._channels > 1 else indata[:, 0]
        self.on_audio(self._resampler.process(mono))
        if self._fifo is not None:
            stereo = indata if self._channels == 2 else np.repeat(indata, 2, axis=1)
            self._fifo.write(stereo * self.volume)

    def _out_cb(self, outdata, frames, time_info, status):
        outdata[:] = self._fifo.read(frames)

    def _close(self):
        for s in (self._in, self._out):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception as e:  # pragma: no cover
                    log.debug("closing stream: %s", e)

    def _finish(self, reason):
        if not self._ended:
            self._ended = True
            if self.on_end:
                self.on_end(reason)

    def stop(self, timeout=None):
        self._ended = True  # a deliberate stop is not a device failure
        self._close()
        if self.on_end:
            self.on_end("stopped")
