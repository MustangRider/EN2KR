"""Speech-to-text with faster-whisper: model catalog and engine."""
import importlib.util
import logging
import os
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import gpu

log = logging.getLogger(__name__)

# faster-whisper imports PyAV only to decode audio *files*; this app always passes numpy
# audio. The release build leaves PyAV out (it ships GPL-licensed x264/x265 codecs and
# ~66 MB), so satisfy the import with an empty module when it is missing.
if importlib.util.find_spec("av") is None:
    sys.modules["av"] = types.ModuleType("av")

LANGUAGES = {
    "en": "영어", "ko": "한국어", "de": "독일어", "fr": "프랑스어", "es": "스페인어",
    "ru": "러시아어", "pl": "폴란드어", "pt": "포르투갈어", "it": "이탈리아어", "nl": "네덜란드어",
    "sv": "스웨덴어", "uk": "우크라이나어", "tr": "터키어", "ja": "일본어", "zh": "중국어",
}


@dataclass
class ModelInfo:
    id: str
    kind: str            # "english" | "multilingual"
    label: str
    params: str
    desc: str
    download_gb: float
    vram_gb: float       # approximate, float16 weights + CUDA runtime
    speed: str           # relative speed on a GPU
    recommended: bool = False


CATALOG = [
    ModelInfo("distil-small.en", "english", "Distil Small (영어)", "166M",
              "가장 가볍고 빠름. 오인식이 잦은 편 (v1 기본값)", 0.33, 0.5, "매우 빠름"),
    ModelInfo("small.en", "english", "Small (영어)", "244M",
              "가벼움. distil-small보다 조금 정확", 0.48, 0.7, "빠름"),
    ModelInfo("distil-large-v3.5", "english", "Distil Large v3.5 (영어)", "756M",
              "영어 고정밀. large급 정확도에 빠른 속도", 1.5, 2.1, "빠름", recommended=True),
    ModelInfo("small", "multilingual", "Small (다국어)", "244M",
              "가벼운 다국어. 짧은 문장의 언어 판별은 약함", 0.48, 0.8, "빠름"),
    ModelInfo("medium", "multilingual", "Medium (다국어)", "769M",
              "정확도와 무게의 중간", 1.5, 2.1, "보통"),
    ModelInfo("large-v3-turbo", "multilingual", "Large v3 Turbo (다국어)", "809M",
              "다국어 정확도·속도 균형. 문장부호를 자주 생략", 1.6, 2.3, "빠름", recommended=True),
    ModelInfo("large-v3", "multilingual", "Large v3 (다국어)", "1550M",
              "최고 정확도. 가장 무겁고 느림", 3.1, 4.0, "느림"),
]
_BY_ID = {m.id: m for m in CATALOG}


def model_info(model_id):
    return _BY_ID.get(model_id)


def resolve_model_id(cfg, cuda):
    model_id = cfg.get("model") or "auto"
    if model_id == "auto" or model_id not in _BY_ID:
        if cfg.get("mode", "english") == "english":
            return "distil-large-v3.5" if cuda else "distil-small.en"
        return "large-v3-turbo" if cuda else "small"
    return model_id


def _repo_id(model_id):
    from faster_whisper.utils import _MODELS
    return _MODELS.get(model_id, model_id)


_ALLOW = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def is_downloaded(model_id):
    try:
        from huggingface_hub import try_to_load_from_cache
        hit = try_to_load_from_cache(_repo_id(model_id), "model.bin")
        return isinstance(hit, str)
    except Exception:
        return False


def _remote_size(repo_id):
    import fnmatch
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo_id, files_metadata=True)
    return sum(s.size or 0 for s in info.siblings
               if any(fnmatch.fnmatch(s.rfilename, pat) for pat in _ALLOW))


def download(model_id, progress=None):
    """Download (or locate) a model; progress(done_bytes, total_bytes).

    huggingface_hub does not expose per-byte progress for snapshot downloads,
    so progress is measured by watching the cache folder grow.
    """
    from huggingface_hub import snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    repo = _repo_id(model_id)
    if is_downloaded(model_id):
        try:  # no network round-trip for a model we already have (works offline)
            return snapshot_download(repo, allow_patterns=_ALLOW, local_files_only=True)
        except Exception:
            pass
    if progress is None:
        return snapshot_download(repo, allow_patterns=_ALLOW)

    stop = threading.Event()
    blobs = os.path.join(HF_HUB_CACHE, "models--" + repo.replace("/", "--"), "blobs")

    def watch():
        try:
            total = _remote_size(repo)
        except Exception:
            total = 0
        while not stop.wait(0.5):
            try:
                done = sum(e.stat().st_size for e in os.scandir(blobs) if e.is_file())
            except OSError:
                done = 0
            progress(done, total)

    t = threading.Thread(target=watch, name="download-progress", daemon=True)
    t.start()
    try:
        return snapshot_download(repo, allow_patterns=_ALLOW)
    finally:
        stop.set()


def choose_language(all_probs, allowed, fallback="en", min_confidence=0.6):
    """Pick the most likely *allowed* language; fall back when unsure.

    all_probs: [(lang, prob), ...] from Whisper language detection.
    Returns (language, confidence among allowed languages).
    """
    allowed = list(dict.fromkeys(list(allowed) + [fallback]))
    probs = {lang: p for lang, p in (all_probs or []) if lang in allowed}
    total = sum(probs.values())
    if not probs or total <= 0:
        return fallback, 0.0
    best = max(probs, key=probs.get)
    conf = probs[best] / total
    if conf < min_confidence:
        return fallback, conf
    return best, conf


@dataclass
class SttResult:
    text: str = ""
    language: str = "en"
    language_conf: float = 1.0
    skipped: Optional[str] = None          # reason when intentionally not transcribed
    skip_note: str = ""                    # user-facing explanation for non-Korean skips
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    latency: float = 0.0
    audio_sec: float = 0.0
    rejected: list = field(default_factory=list)  # low-confidence segment texts
    via: str = ""                          # "fallback": the local stand-in handled it during a Groq limit


# ---------------- cloud speech recognition (Groq) ----------------

GROQ_URL = "https://api.groq.com/openai/v1"
CLOUD_MODELS = {
    "whisper-large-v3": "정확도 우선 (추천)",
    "whisper-large-v3-turbo": "조금 더 빠름, 문장부호를 자주 빼먹음",
}
CLOUD_FREE_RPM = 20       # Groq free plan, counted per account (every key of the account shares it)
FALLBACK_IDLE_SEC = 300   # unload the local stand-in once Groq has worked this long without it
LIMIT_LABELS = {"minute": "분당 요청 한도", "hour": "시간당 음성 길이 한도", "day": "일일 한도"}


def fmt_wait(sec):
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{(sec + 59) // 60}분"
    return f"약 {(sec + 1800) // 3600}시간"


class RequestPacer:
    """Token bucket that keeps cloud requests under a requests-per-minute limit.

    ``burst`` requests may go out back to back, then one every 60/(rpm-burst) s.
    burst + refill*60 == rpm, so no 60-second window holds more than rpm requests
    whatever pattern the speech comes in. A busy stretch therefore costs a few
    seconds of delay instead of hitting the limit and losing up to a minute of speech.
    Utterances are not merged across speakers: Whisper detects one language per
    request, and a merged English + Korean clip came back entirely in Korean.
    """

    def __init__(self, rpm=CLOUD_FREE_RPM, clock=time.monotonic):
        self.rpm = max(5, int(rpm))
        self.burst = max(1, self.rpm * 3 // 10)   # 20 rpm -> 6 at once, then one per ~4.3 s
        self.rate = (self.rpm - self.burst) / 60.0
        self.clock = clock
        self._tokens = float(self.burst)
        self._t = clock()

    def _refill(self):
        now = self.clock()
        self._tokens = min(float(self.burst), self._tokens + (now - self._t) * self.rate)
        self._t = now

    def wait_time(self):
        self._refill()
        return 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.rate

    def record(self):
        self._refill()
        self._tokens -= 1


def _limit_kind(message, wait):
    """Which Groq limit a 429 hit: 'minute', 'hour' (audio seconds) or 'day'."""
    text = (message or "").lower()
    for words, kind in (("per day", "day"), ("per hour", "hour"), ("per minute", "minute")):
        if words in text:
            return kind
    return "minute" if wait <= 120 else "hour" if wait <= 3600 else "day"


_WHISPER_LANG_NAMES = {
    "english": "en", "korean": "ko", "german": "de", "french": "fr", "spanish": "es", "russian": "ru",
    "polish": "pl", "portuguese": "pt", "italian": "it", "dutch": "nl", "swedish": "sv",
    "ukrainian": "uk", "turkish": "tr", "japanese": "ja", "chinese": "zh",
}


class CloudError(Exception):
    pass


class CloudAuthError(CloudError):
    pass


class CloudQuotaError(CloudError):
    def __init__(self, message, retry_after, kind="minute"):
        super().__init__(message)
        self.retry_after = retry_after
        self.kind = kind


class CloudTransientError(CloudError):
    pass


def hangul_ratio(text):
    """Share of Hangul among the letters of a transcript.

    Groq's language label is unreliable for Korean-accented English (it said
    "Korean" for 7 of 8 accented English clips) while the transcript itself
    stayed in Latin letters, so the script decides what is Korean speech.
    """
    hangul = sum(1 for c in text if "가" <= c <= "힣" or "ㄱ" <= c <= "ㆎ")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return hangul / (hangul + latin) if hangul + latin else 0.0


def _retry_after(resp, default=60.0):
    for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-audio-seconds"):
        value = resp.headers.get(key)
        if not value:
            continue
        try:
            return float(value)
        except ValueError:
            pass
        total, num = 0.0, ""
        for ch in value:  # Groq formats like "6m28.8s" / "43.2s"
            if ch.isdigit() or ch == ".":
                num += ch
            elif num:
                total += float(num) * {"h": 3600, "m": 60, "s": 1}.get(ch, 0)
                num = ""
        if total:
            return total
    return default


class GroqStt:
    """Whisper on Groq's servers: no VRAM use, ~0.3-0.9 s per utterance from Korea."""

    def __init__(self, key, model="whisper-large-v3"):
        import requests
        self.key = key.strip()
        self.model = model if model in CLOUD_MODELS else "whisper-large-v3"
        self.remaining = None
        self._http = requests.Session()

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}"}

    def check(self):
        import requests
        try:
            r = self._http.get(f"{GROQ_URL}/models", headers=self._headers(), timeout=(3.05, 10))
        except requests.RequestException as e:
            raise CloudTransientError(f"Groq에 연결하지 못했습니다: {e}") from e
        if r.status_code in (401, 403):
            raise CloudAuthError("Groq API 키가 올바르지 않습니다")
        if r.status_code != 200:
            raise CloudTransientError(f"Groq 응답 오류 HTTP {r.status_code}")
        ids = {m.get("id") for m in r.json().get("data", [])}
        if self.model not in ids:
            raise CloudError(f"이 키로는 {self.model} 모델을 쓸 수 없습니다")

    def transcribe(self, audio, language=None, prompt=None):
        import io
        import wave
        import requests

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        data = {"model": self.model, "response_format": "verbose_json", "temperature": "0"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        try:
            r = self._http.post(f"{GROQ_URL}/audio/transcriptions", headers=self._headers(),
                                files={"file": ("speech.wav", buf.getvalue(), "audio/wav")},
                                data=data, timeout=(3.05, 15))
        except requests.RequestException as e:
            raise CloudTransientError(f"Groq 연결 실패: {e}") from e
        remaining = r.headers.get("x-ratelimit-remaining-requests")
        if remaining is not None and remaining.isdigit():
            self.remaining = int(remaining)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            raise CloudAuthError("Groq API 키가 올바르지 않습니다")
        if r.status_code == 429:
            wait = _retry_after(r)
            try:
                message = (r.json().get("error") or {}).get("message", "")
            except ValueError:
                message = r.text
            kind = _limit_kind(message, wait)
            raise CloudQuotaError(f"Groq {LIMIT_LABELS[kind]}에 도달했습니다 ({fmt_wait(wait)} 후 재개)", wait, kind)
        raise CloudTransientError(f"Groq 응답 오류 HTTP {r.status_code}: {r.text[:120]}")


class SttEngine:
    MAX_NO_SPEECH = 0.6
    MIN_AVG_LOGPROB = -1.0
    KOREAN_SKIP_MIN = 0.85   # raw Whisper probability needed before discarding speech as Korean

    # English-only models cannot tell languages apart: fed Korean, they *translate*
    # it into English. A tiny multilingual model is used only as a Korean detector.
    # Tiny also scores strongly Korean-accented English as Korean (0.89 on our
    # sample), so a mid score only skips when the English transcript is unsure too
    # (Korean speech: avg_logprob -0.35..-0.70, accented English: -0.17).
    # The detector is optional: GPU only, loaded in the background after the main model,
    # and switched off (with a notice) if it can't be downloaded, loaded or run.
    GATE_MODEL = "tiny"
    GATE_SURE = 0.98
    GATE_LIKELY = 0.6
    GATE_EN_LOGPROB = -0.3

    def __init__(self):
        self.model = None
        self.gate = None
        self.cloud = None
        self.model_id = None
        self.device = None
        self.compute_type = None
        self.cfg = {}
        self.gate_state = "off"   # off (not needed) | loading | on | failed | cpu
        self._gate_done = threading.Event()
        self._gen = 0             # bumped on every load; stale detector threads drop their result
        self._notice = self._changed = self._progress = None
        self._cloud_paused_until = 0.0
        self.pacer = RequestPacer()
        self.limit = None          # (kind, until) of the Groq limit last hit
        # Local stand-in used while Groq is over a limit (opt-in, cloud engine only).
        self._fb = None
        self._fb_state = "off"     # off (not configured) | idle | loading | ready | failed
        self._fb_preload = False
        self._fb_used = 0.0
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.model is not None or self.cloud is not None

    def describe(self):
        if self.cloud is not None:
            left = f" · 오늘 남은 요청 {self.cloud.remaining:,}" if self.cloud.remaining is not None else ""
            limit = self.limit_state()
            if limit:
                kind, _, fb = limit
                doing = {"ready": "내 PC로 대신 인식 중", "idle": "대신 쓸 모델 준비 중",
                         "loading": "대신 쓸 모델 준비 중"}.get(fb, "인식 멈춤")
                left = f" · {LIMIT_LABELS[kind]} 도달 · {doing}"
            return f"Groq {self.cloud.model} · 클라우드{left}"
        if not self.model:
            return "로드 안 됨"
        extra = {"on": " · 한국어 판별", "loading": " · 한국어 판별 준비 중",
                 "failed": " · 한국어 판별 꺼짐", "cpu": " · 한국어 판별 꺼짐"}.get(self.gate_state, "")
        return f"{self.model_id} · {self.device.upper()} · {self.compute_type}{extra}"

    def wait_gate(self, timeout=None):
        """Block until the Korean detector finished loading (or was never needed)."""
        return self._gate_done.wait(timeout)

    def load(self, cfg, progress=None, notice=None, changed=None):
        """Load per cfg (the 'stt' settings section). Returns a list of warnings.

        notice(msg) and changed() are called later from the detector thread
        (detector failed / detector state changed).
        """
        self._notice, self._changed, self._progress = notice, changed, progress
        if cfg.get("engine") == "cloud":
            return self._load_cloud(cfg)
        from faster_whisper import WhisperModel

        warnings = []
        cuda = cfg.get("device", "auto") != "cpu" and gpu.cuda_device_count() > 0
        model_id = resolve_model_id(cfg, cuda)
        path = download(model_id, progress)
        attempts = ([("cuda", "float16")] if cuda else []) + [("cpu", "int8")]

        last_error = None
        for device, compute in attempts:
            try:
                model = WhisperModel(path, device=device, compute_type=compute,
                                     cpu_threads=min(4, os.cpu_count() or 4))
                # Some GPU failures only surface on first use.
                list(model.transcribe(np.zeros(16000, np.float32), language="en", beam_size=1)[0])
            except Exception as e:
                last_error = e
                log.warning("Loading %s on %s/%s failed: %s", model_id, device, compute, e)
                if device == "cuda":
                    warnings.append(f"GPU로 모델을 불러오지 못해 CPU로 실행합니다. ({e})")
                continue
            info = _BY_ID.get(model_id)
            wants_gate = bool(info and info.kind == "english" and cfg.get("skip_korean", True))
            with self._lock:
                self._gen += 1
                gen = self._gen
                self.model, self.gate, self.cloud = model, None, None
                self._fb, self._fb_state, self.limit = None, "off", None
                self.model_id, self.device, self.compute_type = model_id, device, compute
                self.cfg = dict(cfg)
                self.gate_state = ("loading" if device == "cuda" else "cpu") if wants_gate else "off"
                self._gate_done.clear() if self.gate_state == "loading" else self._gate_done.set()
            if self.gate_state == "loading":
                threading.Thread(target=self._load_gate, args=(gen, compute), name="stt-gate", daemon=True).start()
            elif self.gate_state == "cpu":
                warnings.append("GPU(CUDA)를 쓸 수 없어 한국어 판별 모델(tiny)을 켜지 않았습니다. 영어 전용 엔진은 한국어를 "
                                "영어로 바꿔 받아 적습니다. 한국어를 건너뛰려면 '인식 엔진'에서 다국어 엔진이나 "
                                "클라우드 · Groq를 고르세요. 둘 다 이 모델 없이 한국어를 가려냅니다.")
            log.info("STT ready: %s", self.describe())
            return warnings
        raise RuntimeError(f"음성 인식 모델을 불러오지 못했습니다: {last_error}")

    def _load_gate(self, gen, compute):
        from faster_whisper import WhisperModel
        try:
            gate = WhisperModel(download(self.GATE_MODEL), device="cuda", compute_type=compute, cpu_threads=2)
            gate.detect_language(np.zeros(16000, np.float32))
        except Exception as e:
            log.warning("Korean detector unavailable: %s", e)
            self._gate_off(gen, f"한국어 판별 모델을 불러오지 못해 한국어 음성 건너뛰기를 끕니다. ({e})")
            return
        with self._lock:
            if gen != self._gen:
                return  # the engine was reloaded meanwhile
            self.gate, self.gate_state = gate, "on"
            self._gate_done.set()
            changed = self._changed
        log.info("Korean detector ready")
        if changed:
            changed()

    def _gate_off(self, gen, message):
        with self._lock:
            if gen != self._gen:
                return
            self.gate, self.gate_state = None, "failed"
            self._gate_done.set()
            notice, changed = self._notice, self._changed
        if notice:
            notice(message)
        if changed:
            changed()

    def _load_cloud(self, cfg):
        cloud_cfg = cfg.get("cloud") or {}
        key = (cloud_cfg.get("key") or "").strip()
        if not key:
            raise RuntimeError("클라우드 인식에 쓸 Groq API 키가 없습니다. '인식 엔진'에서 키를 입력하세요.")
        client = GroqStt(key, cloud_cfg.get("model", "whisper-large-v3"))
        try:
            client.check()
        except CloudError as e:
            raise RuntimeError(str(e)) from e
        fallback = cloud_cfg.get("fallback_model") or ""
        with self._lock:
            self._gen += 1  # a detector still loading for a previous local model is dropped
            gen = self._gen
            self.model = self.gate = None  # frees any local model's VRAM
            self.gate_state = "off"
            self._gate_done.set()
            self.cloud = client
            self.model_id, self.device, self.compute_type = client.model, "cloud", ""
            self._cloud_paused_until = 0.0
            self.pacer = RequestPacer(cloud_cfg.get("rpm") or CLOUD_FREE_RPM)
            self.limit = None
            self._fb = None
            self._fb_state = "idle" if fallback in _BY_ID else "off"
            self._fb_preload = bool(cloud_cfg.get("fallback_preload")) and self._fb_state == "idle"
            if self._fb_preload:
                self._fb_state = "loading"
            self.cfg = dict(cfg)
        if self._fb_state != "off":
            # Download now so the first limit doesn't wait for a 1.5 GB model; load now only if asked to.
            threading.Thread(target=self._prepare_fallback, args=(gen, self._fb_preload),
                             name="stt-fallback", daemon=True).start()
        log.info("STT ready: %s", self.describe())
        return []

    # ---------- local stand-in while Groq is over a limit ----------
    def _fallback_cfg(self, cfg):
        model_id = (cfg.get("cloud") or {}).get("fallback_model")
        out = {k: cfg[k] for k in ("language", "auto_languages", "skip_korean", "beam_size", "use_context")
               if k in cfg}
        out.update(engine="local", model=model_id, device=cfg.get("device", "auto"))
        if _BY_ID[model_id].kind == "english":
            out["mode"] = "english"
        elif cfg.get("mode") in ("fixed", "auto"):
            out["mode"] = cfg["mode"]
        else:  # English mode with a multilingual stand-in: fixed English, still checking for Korean
            out.update(mode="fixed", language="en")
        return out

    def _prepare_fallback(self, gen, load):
        try:
            download(self._fallback_cfg(self.cfg)["model"], self._progress)
        except Exception as e:
            log.warning("Fallback model download failed: %s", e)  # retried when it is needed
        if load:
            self._load_fallback(gen)

    def _load_fallback(self, gen):
        sub = SttEngine()
        try:
            warnings = sub.load(self._fallback_cfg(self.cfg), progress=self._progress, notice=self._notice)
        except Exception as e:
            log.warning("Fallback model unavailable: %s", e)
            with self._lock:
                if gen != self._gen:
                    return
                self._fb_state = "failed"
                notice, changed = self._notice, self._changed
            if notice:
                notice(f"Groq 한도 동안 쓸 내 PC 모델을 불러오지 못했습니다. ({e})")
            if changed:
                changed()
            return
        with self._lock:
            if gen != self._gen:
                return  # the engine was reloaded meanwhile; dropping sub frees its VRAM
            self._fb, self._fb_state, self._fb_used = sub, "ready", time.monotonic()
            notice, changed = self._notice, self._changed
        log.info("Fallback ready: %s", sub.describe())
        for w in warnings:
            if notice:
                notice(w)
        if changed:
            changed()

    def _unload_idle_fallback(self):
        with self._lock:
            if self._fb is None or self._fb_preload or time.monotonic() - self._fb_used < FALLBACK_IDLE_SEC:
                return
            self._fb, self._fb_state = None, "idle"
            notice, changed = self._notice, self._changed
        log.info("Fallback unloaded")
        if notice:
            notice("Groq가 다시 정상이라 대신 쓰던 내 PC 모델을 내리고 VRAM을 비웠습니다")
        if changed:
            changed()

    def limit_state(self):
        """(kind, until, fallback state) while a Groq limit is in force, else None."""
        with self._lock:
            if self.cloud is None or self.limit is None or self.limit[1] <= time.monotonic():
                return None
            return self.limit[0], self.limit[1], self._fb_state

    def cloud_wait(self):
        """Seconds until the next cloud request may go out (0 for local engines and while limited)."""
        with self._lock:
            if self.cloud is None or self._cloud_paused_until > time.monotonic():
                return 0.0
            return self.pacer.wait_time()

    def _while_limited(self, audio, prev_text, reason, note):
        with self._lock:
            fb, state, gen = self._fb, self._fb_state, self._gen
            if state == "idle":
                self._fb_state = "loading"
        if state == "idle":
            threading.Thread(target=self._load_fallback, args=(gen,), name="stt-fallback", daemon=True).start()
        if fb is None:
            if state in ("idle", "loading"):
                note += " · 대신 쓸 내 PC 모델 준비 중"
            return SttResult(audio_sec=len(audio) / 16000, language="", skipped=reason, skip_note=note)
        self._fb_used = time.monotonic()
        r = fb.transcribe(audio, prev_text)
        r.via = "fallback"
        return r

    def _transcribe_cloud(self, cloud, audio, prev_text, cfg):
        t0 = time.perf_counter()
        wait = self._cloud_paused_until - time.monotonic()
        if wait > 0:
            return self._while_limited(audio, prev_text, "cloud_paused", f"Groq 한도 대기 중 ({fmt_wait(wait)} 남음)")
        result = SttResult(audio_sec=len(audio) / 16000, language="")
        # Groq picks the language itself; Korean is judged from the transcript's script.
        prompt = prev_text[-200:] if cfg.get("use_context", True) and prev_text else None
        self.pacer.record()
        try:
            reply = cloud.transcribe(_normalize(audio), None, prompt)
        except CloudQuotaError as e:
            self._cloud_paused_until = time.monotonic() + e.retry_after
            self.limit = (e.kind, self._cloud_paused_until)
            return self._while_limited(audio, prev_text, "cloud_quota", str(e))
        except CloudTransientError as e:
            result.skipped, result.skip_note = "cloud_error", str(e)
            return result
        self._unload_idle_fallback()
        texts, logprobs, nospeech = [], [], []
        for s in reply.get("segments") or []:
            txt = (s.get("text") or "").strip()
            if not txt:
                continue
            if s.get("no_speech_prob", 0) < self.MAX_NO_SPEECH and s.get("avg_logprob", 0) > self.MIN_AVG_LOGPROB:
                texts.append(txt)
                logprobs.append(s.get("avg_logprob", 0))
                nospeech.append(s.get("no_speech_prob", 0))
            else:
                result.rejected.append(txt)
        text = " ".join(texts).strip()
        if cfg.get("skip_korean", True) and text and hangul_ratio(text) >= 0.5:
            return self._skipped(result, 1.0, t0)
        result.text = text
        result.language = _WHISPER_LANG_NAMES.get((reply.get("language") or "").lower(), "")
        if hangul_ratio(text) < 0.5 and result.language == "ko":
            result.language = "en"  # English transcript mislabelled as Korean (accented speech)
        if prompt and text and text.lower() in prompt.lower():
            result.rejected.append(text)
            result.text = ""
        if logprobs:
            result.avg_logprob = float(np.mean(logprobs))
            result.no_speech_prob = float(np.mean(nospeech))
        result.latency = time.perf_counter() - t0
        return result

    def update_options(self, cfg):
        """Apply options that don't need a reload (language mode, context...)."""
        with self._lock:
            self.cfg = dict(cfg)

    def _skipped(self, result, ko, t0):
        result.language, result.language_conf, result.skipped = "ko", ko, "korean"
        result.text = ""
        result.latency = time.perf_counter() - t0
        return result

    def transcribe(self, audio, prev_text=None):
        with self._lock:
            model, gate, cfg, model_id, cloud = self.model, self.gate, dict(self.cfg), self.model_id, self.cloud
            gen = self._gen
        if cloud is not None:
            return self._transcribe_cloud(cloud, audio, prev_text, cfg)
        if model is None:
            raise RuntimeError("STT model not loaded")
        t0 = time.perf_counter()
        audio = _normalize(audio)
        info_kind = _BY_ID.get(model_id).kind if model_id in _BY_ID else "multilingual"
        mode = cfg.get("mode", "english")
        if info_kind == "english":
            mode = "english"
        skip_korean = cfg.get("skip_korean", True)
        lang = "en" if mode == "english" else cfg.get("language", "en")
        opts = dict(beam_size=int(cfg.get("beam_size", 5)), condition_on_previous_text=False,
                    without_timestamps=True, vad_filter=False)
        if cfg.get("use_context", True) and prev_text:
            # faster-whisper resets context on every call; initial_prompt carries it over.
            opts["initial_prompt"] = prev_text[-200:]

        result = SttResult(audio_sec=len(audio) / 16000, language=lang)
        gate_ko = None
        if mode == "english":
            if gate is not None and skip_korean:
                try:
                    _, _, probs = gate.detect_language(audio)
                except Exception as e:
                    log.warning("Korean detector failed, switching it off: %s", e)
                    self._gate_off(gen, f"한국어 판별 중 오류가 나서 한국어 음성 건너뛰기를 끕니다. ({e})")
                else:
                    gate_ko = dict(probs or []).get("ko", 0.0)
                    if gate_ko >= self.GATE_SURE:
                        return self._skipped(result, gate_ko, t0)
            segments, info = model.transcribe(audio, language="en", **opts)
        elif mode == "auto" or skip_korean:
            # Let Whisper detect the language first (fixed mode needs it only to spot Korean).
            segments, info = model.transcribe(audio, language=None, **opts)
            probs = info.all_language_probs or []
            raw_ko = dict(probs).get("ko", 0.0)
            if mode == "auto":
                allowed = cfg.get("auto_languages", ["en"])
                fallback = cfg.get("language", "en")
                lang, conf = choose_language(probs, allowed, fallback=fallback)
                if lang == "ko" and skip_korean and raw_ko < self.KOREAN_SKIP_MIN:
                    # Korean-accented English is often scored as "ko": don't drop it, pick the next language.
                    lang, conf = choose_language(probs, [x for x in allowed if x != "ko"],
                                                 fallback=fallback if fallback != "ko" else "en", min_confidence=0.0)
                result.language_conf = conf
            if skip_korean and raw_ko >= self.KOREAN_SKIP_MIN:
                return self._skipped(result, raw_ko, t0)
            result.language = lang
            if lang != info.language:
                segments, info = model.transcribe(audio, language=lang, **opts)
        else:
            segments, info = model.transcribe(audio, language=lang, **opts)

        texts, logprobs, nospeech, all_logprobs = [], [], [], []
        for s in segments:
            txt = s.text.strip()
            if not txt:
                continue
            all_logprobs.append(s.avg_logprob)
            if s.no_speech_prob < self.MAX_NO_SPEECH and s.avg_logprob > self.MIN_AVG_LOGPROB:
                texts.append(txt)
                logprobs.append(s.avg_logprob)
                nospeech.append(s.no_speech_prob)
            else:
                result.rejected.append(txt)
        if (gate_ko is not None and gate_ko >= self.GATE_LIKELY and all_logprobs
                and float(np.mean(all_logprobs)) < self.GATE_EN_LOGPROB):
            return self._skipped(result, gate_ko, t0)
        result.text = " ".join(texts).strip()
        if opts.get("initial_prompt") and result.text and result.text.lower() in opts["initial_prompt"].lower():
            result.rejected.append(result.text)  # prompt echoed back on unclear audio
            result.text = ""
        if logprobs:
            result.avg_logprob = float(np.mean(logprobs))
            result.no_speech_prob = float(np.mean(nospeech))
        result.latency = time.perf_counter() - t0
        return result


def _normalize(audio):
    audio = audio.astype(np.float32, copy=False) - float(np.mean(audio))
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if 0 < peak < 0.5:
        audio = audio * min(0.5 / peak, 8.0)  # bounded gain: don't blow up noise
    return audio
