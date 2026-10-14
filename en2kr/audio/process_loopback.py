"""Capture the audio of a single process tree (no virtual cable needed).

Uses WASAPI process loopback (ActivateAudioInterfaceAsync with
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK), the mechanism behind OBS's
"Application Audio Capture". Documented for Windows 10 build 20348+, verified
working on Windows 10 22H2 (build 19045). The game is untouched: only its PID
is handed to the Windows audio service.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

import numpy as np

from . import SAMPLE_RATE
from .com import GUID, HRESULT, Apartment, ComError, check, guid, kernel32, release, vcall
from .sessions import process_alive

log = logging.getLogger(__name__)

IID_IUnknown = guid("{00000000-0000-0000-C000-000000000046}")
IID_IAgileObject = guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
IID_CompletionHandler = guid("{41D949AB-9862-444A-80F6-C261334DA5EB}")
IID_IAudioClient = guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 3
E_NOINTERFACE = 0x80004002 - (1 << 32)


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", wt.ULONG), ("pBlobData", ctypes.c_void_p)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.WORD), ("r2", wt.WORD), ("r3", wt.WORD), ("blob", _Blob)]


class _LoopbackParams(ctypes.Structure):
    _fields_ = [("TargetProcessId", wt.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("Loopback", _LoopbackParams)]


class _WaveFormatEx(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", wt.WORD), ("nChannels", wt.WORD), ("nSamplesPerSec", wt.DWORD),
                ("nAvgBytesPerSec", wt.DWORD), ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD),
                ("cbSize", wt.WORD)]


_QI = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_REF = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)
_DONE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("ActivateCompleted", _DONE)]


class _Handler(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_HandlerVtbl))]


class _Activation:
    """IActivateAudioInterfaceCompletionHandler implemented in ctypes.

    Instances must outlive the async operation, so the capture object keeps a
    reference until its thread exits.
    """

    def __init__(self):
        self.done = threading.Event()
        self.hr = None
        self.client = None
        self._vtbl = _HandlerVtbl(_QI(self._qi), _REF(lambda this: 1), _REF(lambda this: 1), _DONE(self._completed))
        self.handler = _Handler(ctypes.pointer(self._vtbl))

    def _qi(self, this, riid, ppv):
        if riid.contents in (IID_IUnknown, IID_IAgileObject, IID_CompletionHandler):
            ppv[0] = this
            return 0
        ppv[0] = None
        return E_NOINTERFACE

    def _completed(self, this, op):
        try:
            hr, unk = HRESULT(), ctypes.c_void_p()
            ret = vcall(op, 3, HRESULT, [ctypes.POINTER(HRESULT), ctypes.POINTER(ctypes.c_void_p)],
                        ctypes.byref(hr), ctypes.byref(unk))
            self.hr = hr.value if ret == 0 else ret
            self.client = unk.value
        except Exception as e:  # never let an exception cross the COM boundary
            log.exception("ActivateCompleted failed: %s", e)
            self.hr = -1
        finally:
            self.done.set()
        return 0


def _activate(pid, include_tree=True):
    act = _Activation()
    params = _ActivationParams(1, _LoopbackParams(pid, 0 if include_tree else 1))
    pv = _PropVariant()
    pv.vt = 65  # VT_BLOB
    pv.blob.cbSize = ctypes.sizeof(params)
    pv.blob.pBlobData = ctypes.addressof(params)
    op = ctypes.c_void_p()
    fn = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
    fn.restype = HRESULT
    fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID), ctypes.POINTER(_PropVariant),
                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    check(fn(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, ctypes.byref(IID_IAudioClient), ctypes.byref(pv),
             ctypes.addressof(act.handler), ctypes.byref(op)), "ActivateAudioInterfaceAsync")
    try:
        if not act.done.wait(5):
            raise TimeoutError("process loopback activation timed out")
    finally:
        release(op.value)
    if act.hr != 0 or not act.client:
        raise ComError("process loopback activation", act.hr or -1)
    return act.client, act


class ProcessLoopbackCapture:
    def __init__(self, pid, process_name, on_audio, on_end=None):
        self.pid = pid
        self.process_name = process_name
        self.on_audio = on_audio
        self.on_end = on_end
        self.error = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._thread = None
        self._keep = None

    @property
    def description(self):
        return f"{self.process_name} (PID {self.pid})"

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, wait=3.0):
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.pid}", daemon=True)
        self._thread.start()
        self._started.wait(wait)
        if self.error:
            raise self.error
        return self

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _run(self):
        reason = "stopped"
        try:
            with Apartment():
                # COM objects must be released before the apartment is torn down.
                client = capture = event = None
                try:
                    client, self._keep = _activate(self.pid)
                    fmt = _WaveFormatEx(WAVE_FORMAT_IEEE_FLOAT, 1, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 32, 0)
                    flags = (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                             | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY)
                    check(vcall(client, 3, HRESULT, [ctypes.c_int, wt.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                                                     ctypes.POINTER(_WaveFormatEx), ctypes.c_void_p],
                                0, flags, 200_000, 0, ctypes.byref(fmt), None), "IAudioClient.Initialize")
                    kernel32.CreateEventW.restype = wt.HANDLE
                    event = kernel32.CreateEventW(None, False, False, None)
                    check(vcall(client, 13, HRESULT, [wt.HANDLE], event), "SetEventHandle")
                    cap = ctypes.c_void_p()
                    check(vcall(client, 14, HRESULT, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                                ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap)), "GetService")
                    capture = cap.value
                    check(vcall(client, 10, HRESULT, []), "IAudioClient.Start")
                    self._started.set()
                    log.info("Process capture started: %s", self.description)
                    reason = self._loop(capture, event)
                except Exception as e:
                    self.error = e
                    reason = f"error: {e}"
                finally:
                    self._started.set()
                    if client:
                        vcall(client, 11, HRESULT, [])  # Stop
                    release(capture)
                    release(client)
                    if event:
                        kernel32.CloseHandle(event)
        except Exception as e:
            self.error = self.error or e
            reason = f"error: {e}"
        finally:
            self._started.set()
            log.info("Process capture ended (%s): %s", reason, self.description)
            if self.on_end:
                self.on_end(reason)

    def _loop(self, capture, event):
        next_check = time.monotonic() + 2.0
        size = wt.UINT()
        data, frames, flags = ctypes.c_void_p(), wt.UINT(), wt.DWORD()
        while not self._stop.is_set():
            kernel32.WaitForSingleObject(event, 100)
            while True:
                if vcall(capture, 5, HRESULT, [ctypes.POINTER(wt.UINT)], ctypes.byref(size)) != 0 or not size.value:
                    break
                hr = vcall(capture, 3, HRESULT,
                           [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.DWORD),
                            ctypes.c_void_p, ctypes.c_void_p],
                           ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags), None, None)
                if hr < 0:
                    return f"error: GetBuffer hr=0x{hr & 0xFFFFFFFF:08X}"
                n = frames.value
                if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                    chunk = np.zeros(n, np.float32)
                else:
                    chunk = np.frombuffer(ctypes.string_at(data, n * 4), np.float32).copy()
                vcall(capture, 4, HRESULT, [wt.UINT], frames)
                if n:
                    self.on_audio(chunk)
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 2.0
                if not process_alive(self.pid, self.process_name):
                    return "target exited"
        return "stopped"
