"""Audio -> VAD -> STT -> translation, each stage on its own thread.

Every worker loop catches its own exceptions and reports them through
``events.status`` instead of dying silently (v1 threads died on the first
error and the app kept showing a VAD lamp with no translations).
"""
import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from . import paths
from .audio import SAMPLE_RATE
from .filters import HallucinationFilter
from .stt import LIMIT_LABELS, SttEngine, fmt_wait
from .transcripts import TranscriptLog
from .translate.service import Job, TranslationService
from .vad import Segmenter, SileroVad

log = logging.getLogger(__name__)

TERMINAL = (".", "?", "!", "…")
MERGE_GAP_SEC = 1.0      # join a sentence fragment with the next one if this close
MAX_HOLD_SEC = 6.0       # never hold a fragment's translation longer than this
MAX_LINE_CHARS = 220
CONTEXT_GAP_SEC = 4.0    # previous text is used as Whisper context if this close
STALL_FLUSH_SEC = 1.0    # close an utterance if audio stops arriving
MAX_BACKLOG_SEC = 10.0   # Groq pacing: a subtitle later than this is useless, drop it
MAX_JOIN_SEC = 28.0      # Groq pacing: a joined request stays inside one 30 s Whisper window
JOIN_GAP_SEC = 0.8       # Groq pacing: a fragment this close is the same speaker going on


def _noop(*args, **kwargs):
    pass


@dataclass
class PipelineEvents:
    status: Callable = _noop        # (component, state, message); state: ok|wait|loading|error|off
    level: Callable = _noop         # (rms 0..1)
    speech: Callable = _noop        # (active)
    stt: Callable = _noop           # (SttLine)
    translation: Callable = _noop   # (Result)
    notice: Callable = _noop        # (message)
    download: Callable = _noop      # (done_bytes, total_bytes)
    auto_ban: Callable = _noop      # (text)
    limit: Callable = _noop         # (message, seconds_left) Groq limit hit; ("", 0) once it is over


@dataclass
class SttLine:
    text: str                 # text shown/translated (may include a merged fragment)
    raw: str                  # what Whisper produced for this utterance
    language: str
    line_id: Optional[int]    # None when not sent to translation
    replace: bool = False
    note: Optional[str] = None  # why it was dropped, if it was
    latency: float = 0.0
    rejected: list = field(default_factory=list)
    via: str = ""               # "fallback": recognised by the local stand-in during a Groq limit


class Pipeline:
    def __init__(self, settings, events=None):
        self.settings = settings
        self.events = events or PipelineEvents()
        self.engine = SttEngine()
        self.filter = HallucinationFilter(settings.get("hallucinations", []), on_auto_ban=self._auto_ban)
        self.translator = TranslationService(self._on_translation, self.events.notice)
        lg = settings.section("logging")
        self.transcripts = TranscriptLog(lg.get("save_transcripts", True), lg.get("retention_days", 14))
        self._audio_q = queue.Queue(maxsize=400)       # ~25 s of 1000-frame chunks
        self._seg_q = queue.Queue(maxsize=8)
        self._running = False
        self._threads = []
        self._source = None
        self._source_lock = threading.Lock()
        self._source_dirty = threading.Event()
        self._last_audio = 0.0
        self._level_acc = []
        self._line_id = 0
        self._last = None          # dict(text, end, lang, line_id, complete)
        self._pending = None       # (Job, end_sample, held_since) awaiting a possible continuation
        self._stt_lock = threading.Lock()
        self._stt_failures = 0
        self._force_cpu = False    # set after repeated GPU failures; not saved to settings
        self._switch_to = None     # PID found playing while the captured one was silent
        self._limit_shown = None   # Groq limit state last reported to the UI
        self._backlog = collections.deque()  # utterances waiting for a Groq request slot
        self.enabled = True        # translation switched on (hotkey); off = audio is dropped before VAD
        self.segmenter = None

    # ---------- lifecycle ----------
    def start(self):
        vad_cfg = self.settings.section("vad")
        self.segmenter = Segmenter(
            SileroVad(paths.VAD_MODEL_PATH), self._on_segment, self.events.speech,
            threshold=vad_cfg["threshold"], silence_sec=vad_cfg["silence_sec"],
            preroll_sec=vad_cfg["preroll_sec"], max_utterance_sec=vad_cfg["max_utterance_sec"],
            min_speech_sec=vad_cfg["min_speech_sec"])
        self.translator.configure(self.settings.snapshot())
        self.translator.start()
        self.transcripts.cleanup()
        self._running = True
        for target, name in ((self._vad_loop, "vad"), (self._stt_loop, "stt"), (self._source_loop, "source")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self._report_translate_status()
        self.reload_stt()

    def stop(self):
        self._running = False
        self._source_dirty.set()
        with self._source_lock:
            if self._source:
                self._source.stop()
                self._source = None
        self.translator.stop()
        for t in self._threads:
            t.join(2)

    # ---------- configuration changes (call from UI thread) ----------
    def apply_vad(self):
        c = self.settings.section("vad")
        self.segmenter.configure(c["threshold"], c["silence_sec"], c["preroll_sec"],
                                 c["max_utterance_sec"], c["min_speech_sec"])

    def apply_translation(self):
        self.translator.configure(self.settings.snapshot())
        self._report_translate_status()

    def apply_filters(self):
        self.filter.set_blocked(self.settings.get("hallucinations", []))

    def apply_logging(self):
        lg = self.settings.section("logging")
        self.transcripts.enabled = lg.get("save_transcripts", True)
        self.transcripts.retention_days = lg.get("retention_days", 14)

    def apply_stt_options(self):
        """Mode/language changes that don't need a new model."""
        if self.engine.ready:
            self.engine.update_options(self.settings.section("stt"))

    def restart_source(self):
        self._source_dirty.set()

    def set_enabled(self, on):
        """Switch translation on/off. Off drops audio before speech detection: no recognition,
        no API calls, no GPU work. What was already recognised still gets translated."""
        self.enabled = bool(on)
        if not on:  # the STT thread empties its own backlog (see _stt_loop)
            while True:
                try:
                    self._seg_q.get_nowait()
                except queue.Empty:
                    break
            self.events.speech(False)
            self.events.level(0.0)
            self.events.status("stt", "off", "번역 꺼짐")
        elif self.engine.ready:
            self._report_stt_ready()

    def reload_stt(self, clear_fallback=False):
        if clear_fallback:
            self._force_cpu = False
        cfg = self.settings.section("stt")
        if self._force_cpu:
            cfg["device"] = "cpu"

        def work():
            with self._stt_lock:
                self.events.status("stt", "loading", "음성 인식 모델 준비 중...")
                try:
                    warnings = self.engine.load(cfg, progress=self.events.download, notice=self.events.notice,
                                                changed=self._report_stt_ready)
                except Exception as e:
                    log.exception("STT load failed")
                    self.events.status("stt", "error", str(e))
                    return
                self._stt_failures = 0
                for w in warnings:
                    self.events.notice(w)
                self._report_stt_ready()

        threading.Thread(target=work, name="stt-load", daemon=True).start()

    def _report_stt_ready(self):
        if not self.enabled:
            self.events.status("stt", "off", "번역 꺼짐")
        elif self.engine.device == "cpu":
            self.events.status("stt", "wait", self.engine.describe() + " — CPU 모드(느림)")
        elif self.engine.limit_state():
            self.events.status("stt", "wait", self.engine.describe())
        else:
            self.events.status("stt", "ok", self.engine.describe())
        self._report_limit()

    def _report_limit(self):
        """Tell the UI (overlay warning) when a Groq limit starts, changes or ends."""
        state = self.engine.limit_state()
        if state == self._limit_shown:
            return
        self._limit_shown = state
        if state is None:
            self.events.limit("", 0.0)
            return
        kind, until, fallback = state
        doing = {"ready": "내 PC 모델로 대신 인식 중", "idle": "대신 쓸 내 PC 모델 준비 중",
                 "loading": "대신 쓸 내 PC 모델 준비 중"}.get(fallback, "그동안 음성 인식을 멈춥니다")
        message = f"Groq {LIMIT_LABELS[kind]} 도달 · {doing}"
        left = until - time.monotonic()
        self.events.notice(f"{message} ({fmt_wait(left)} 후 재개)")
        self.events.limit(message, left)

    def _report_translate_status(self):
        labels = self.translator.provider_labels
        if labels:
            self.events.status("translate", "ok", " → ".join(labels))
        else:
            self.events.status("translate", "error", "번역 API 미설정")

    # ---------- audio source ----------
    def _on_audio(self, chunk):
        self._last_audio = time.monotonic()
        try:
            self._audio_q.put_nowait(chunk)
        except queue.Full:
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                pass
            self._audio_q.put_nowait(chunk)

    def _on_source_end(self, reason):
        if self._running and reason != "stopped":
            self._source_dirty.set()

    def _source_loop(self):
        from .audio import device, process_loopback, sessions

        retry_at = 0.0
        while self._running:
            dirty = self._source_dirty.wait(1.0)
            self._source_dirty.clear()
            if not self._running:
                return
            with self._source_lock:
                current = self._source
            if current is not None and current.running and not dirty:
                if self._should_switch_instance(current):
                    dirty = True
                else:
                    continue
            if current is not None:
                current.stop()
                with self._source_lock:
                    self._source = None
                if self.segmenter:
                    self.segmenter.reset()
                self._last = None
            if not dirty and time.monotonic() < retry_at:
                continue
            cfg = self.settings.section("audio")
            if cfg.get("mode") == "none":  # offline input via feed_file_audio()
                self.events.status("audio", "off", "파일 입력")
                retry_at = time.monotonic() + 3600
                continue
            try:
                if cfg.get("mode") == "device":
                    key = cfg.get("device_name") or ""
                    if not key:
                        self.events.status("audio", "error", "입력 장치가 선택되지 않았습니다")
                        retry_at = time.monotonic() + 5
                        continue
                    src = device.DeviceCapture(key, self._on_audio, self._on_source_end).start()
                else:
                    name = cfg.get("process_name") or ""
                    if not name:
                        self.events.status("audio", "wait", "번역할 프로그램을 선택하세요")
                        retry_at = time.monotonic() + 2
                        continue
                    procs = sessions.list_processes()
                    pid, self._switch_to = self._switch_to, None
                    if pid is None:
                        audio = {a.pid: a for a in sessions.list_audio_apps()}
                        pid = sessions.resolve_target_pid(name, procs=procs, audio=audio)
                    if pid is None:
                        self.events.status("audio", "wait", f"{name} 실행을 기다리는 중")
                        retry_at = time.monotonic() + 2
                        continue
                    exe = next((p.name for p in procs if p.pid == pid), name)
                    src = process_loopback.ProcessLoopbackCapture(pid, exe, self._on_audio, self._on_source_end).start()
            except Exception as e:
                log.warning("Audio source start failed: %s", e)
                self.events.status("audio", "error", f"오디오 연결 실패: {e}")
                retry_at = time.monotonic() + 5
                continue
            with self._source_lock:
                self._source = src
            self.events.status("audio", "ok", src.description)

    def _should_switch_instance(self, current, every=3.0):
        """If another instance of the target exe starts playing, follow it."""
        from .audio import sessions
        now = time.monotonic()
        if now < getattr(self, "_next_switch_check", 0) or not hasattr(current, "pid"):
            return False
        self._next_switch_check = now + every
        try:
            audio = {a.pid: a for a in sessions.list_audio_apps()}
            name = self.settings.get("audio.process_name") or current.process_name
            other = sessions.playing_elsewhere(name, current.pid, audio=audio)
        except Exception as e:
            log.debug("instance check failed: %s", e)
            return False
        if other:
            log.info("Switching capture from PID %s to playing PID %s", current.pid, other)
            self._switch_to = other
        return bool(other)

    # ---------- VAD ----------
    def _vad_loop(self):
        while self._running:
            try:
                try:
                    chunk = self._audio_q.get(timeout=0.25)
                except queue.Empty:
                    if self.segmenter.in_speech and time.monotonic() - self._last_audio > STALL_FLUSH_SEC:
                        self.segmenter.flush()
                    continue
                if not self.enabled:
                    if self.segmenter.in_speech:
                        self.segmenter.reset()  # don't finish a sentence that was cut by switching off
                    continue
                self._level_acc.append(float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0)
                if len(self._level_acc) >= 4:
                    self.events.level(max(self._level_acc))
                    self._level_acc = []
                self.segmenter.feed(chunk)
            except Exception as e:
                log.exception("VAD loop error")
                self.events.status("audio", "error", f"음성 감지 오류: {e}")
                time.sleep(0.5)

    def _on_segment(self, audio, info):
        try:
            self._seg_q.put_nowait((audio, info))
        except queue.Full:
            try:
                self._seg_q.get_nowait()
            except queue.Empty:
                pass
            self._seg_q.put_nowait((audio, info))
            self.events.notice("음성 인식이 밀려 오래된 문장 하나를 건너뛰었습니다")

    # ---------- STT ----------
    def _stt_loop(self):
        while self._running:
            item = self._next_segment(0.25)
            if item is None:
                self._flush_pending()
                if self._limit_shown and self._limit_shown[1] <= time.monotonic():
                    self._report_stt_ready()  # the Groq limit ran out while nobody spoke
                continue
            if not self.enabled:
                self._backlog.clear()
                continue
            if not self.engine.ready:
                continue
            try:
                wait = self.engine.cloud_wait()
                if wait > 0:
                    self._wait_for_slot(wait)
                item = self._join_continuations(item)
                if item is not None:
                    self._handle_segment(*item)
                self._stt_failures = 0
            except Exception as e:
                self._stt_failures += 1
                log.exception("STT error")
                self.events.status("stt", "error", f"음성 인식 오류: {e}")
                if self._stt_failures == 3 and self.engine.device == "cuda":
                    self.events.notice("GPU 음성 인식이 계속 실패해 CPU로 전환합니다")
                    self._force_cpu = True
                    self.reload_stt()

    # ---------- Groq pacing ----------
    def _next_segment(self, timeout):
        if self._backlog:
            return self._backlog.popleft()
        try:
            audio, info = self._seg_q.get(timeout=timeout)
        except queue.Empty:
            return None
        info.setdefault("queued", time.monotonic())
        return audio, info

    def _wait_for_slot(self, wait):
        """Groq requests are being paced: keep taking utterances meanwhile so none is lost."""
        self.events.status("stt", "wait", "Groq 분당 한도에 맞춰 순서대로 보내는 중")
        deadline = time.monotonic() + wait
        while self._running and time.monotonic() < deadline:
            try:
                audio, info = self._seg_q.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                self._flush_pending()
                continue
            info.setdefault("queued", time.monotonic())
            self._backlog.append((audio, info))

    def _join_continuations(self, item):
        """Drop what waited too long; join the fragments that directly continue this utterance.

        Only fragments as close as JOIN_GAP_SEC (the same speaker going on) share a
        request: Whisper detects one language per request, so joining different
        speakers could turn English speech into Korean or the other way round.
        """
        now = time.monotonic()
        dropped = 0
        while item is not None and now - item[1].get("queued", now) > MAX_BACKLOG_SEC:
            dropped += 1
            item = self._backlog.popleft() if self._backlog else None
        if dropped:
            self.events.notice(f"Groq 분당 한도 때문에 {MAX_BACKLOG_SEC:.0f}초 넘게 밀린 문장 {dropped}개를 건너뛰었습니다")
        if item is None or not self._backlog:
            return item
        parts, info = [item[0]], dict(item[1])
        total = len(item[0])
        while self._backlog:
            audio, nxt = self._backlog[0]
            gap = nxt["start_sample"] - info["end_sample"]
            if gap > JOIN_GAP_SEC * SAMPLE_RATE or total + max(gap, 0) + len(audio) > MAX_JOIN_SEC * SAMPLE_RATE:
                break
            self._backlog.popleft()
            parts += [np.zeros(max(gap, 0), np.float32), audio]
            total += max(gap, 0) + len(audio)
            info["end_sample"] = nxt["end_sample"]
        return (np.concatenate(parts) if len(parts) > 1 else parts[0]), info

    # ---------- results ----------
    def _handle_segment(self, audio, info):
        start = info["start_sample"] / SAMPLE_RATE
        end = info["end_sample"] / SAMPLE_RATE
        prev = self._last
        context = prev["text"] if prev and start - prev["end"] <= CONTEXT_GAP_SEC else None
        r = self.engine.transcribe(audio, prev_text=context)
        if self.engine.ready and (self._stt_failures or self.engine.device == "cloud"):
            self._report_stt_ready()  # cloud: refresh the remaining-request count and limit state
        if r.skipped in ("cloud_quota", "cloud_paused"):
            self.events.status("stt", "wait", r.skip_note)
        line = SttLine(r.text, r.text, r.language, None, latency=r.latency, rejected=r.rejected, via=r.via)
        if r.skipped == "korean":
            line.note = "한국어 음성 건너뜀"
        elif r.skipped:
            line.note = r.skip_note or r.skipped
        elif not r.text:
            line.note = "신뢰도 낮음" if r.rejected else None
        else:
            line.note = self.filter.check(r.text, r.avg_logprob, r.no_speech_prob)
        if line.note or not r.text:
            if line.note:
                self.events.stt(line)
            return

        merge = (prev is not None and not prev["complete"] and prev["lang"] == r.language
                 and start - prev["end"] <= MERGE_GAP_SEC and len(prev["text"]) < MAX_LINE_CHARS)
        if not merge:
            self._flush_pending(force=True)
        if merge:
            line.text = f"{prev['text']} {r.text}"
            line.line_id, line.replace = prev["line_id"], True
        else:
            self._line_id += 1
            line.line_id = self._line_id
        complete = line.text.rstrip().endswith(TERMINAL)
        self._last = {"text": line.text, "end": end, "lang": r.language, "line_id": line.line_id,
                      "complete": complete}
        self.events.stt(line)  # show the original right away
        job = Job(line.line_id, line.text, r.language, line.replace)
        if complete:
            self._pending = None
            self.translator.submit(job)
        else:
            # Hold the translation briefly: if the speaker continues, the merged
            # sentence is translated once instead of once per fragment.
            held = self._pending[2] if self._pending and merge else time.monotonic()
            self._pending = (job, info["end_sample"], held)
            self._flush_pending()

    def _flush_pending(self, force=False):
        pending = self._pending
        if not pending:
            return
        job, end_sample, held = pending
        if not force and not self._seg_q.empty() and time.monotonic() - held <= MAX_HOLD_SEC:
            return  # the next utterance is already waiting; it will merge or force a flush
        seg = self.segmenter
        gap = MERGE_GAP_SEC * SAMPLE_RATE
        no_continuation = ((not seg.in_speech and seg.position - end_sample > gap)
                           or (seg.in_speech and seg.utterance_start - end_sample > gap))
        if force or no_continuation or time.monotonic() - held > MAX_HOLD_SEC:
            self._pending = None
            self.translator.submit(job)

    # ---------- translation ----------
    def _on_translation(self, res):
        if res.text and not res.error:
            self.transcripts.write(res.lang, res.source, res.text)
        self.events.translation(res)

    def _auto_ban(self, text):
        self.events.auto_ban(text)

    # ---------- offline input (tests / --selftest) ----------
    def feed_file_audio(self, audio, realtime=False, chunk=1000):
        for i in range(0, len(audio), chunk):
            self._on_audio(audio[i:i + chunk])
            if realtime:
                time.sleep(chunk / SAMPLE_RATE)
