"""Speech-to-text with faster-whisper: model catalog and engine."""
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import gpu, paths

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
    recommended: bool = False
    vram_mb: Optional[int] = None   # measured, float16, includes CUDA context
    rtf: Optional[float] = None     # processing time / audio length (lower is faster)


CATALOG = [
    ModelInfo("distil-small.en", "english", "Distil Small (영어)", "166M",
              "가장 가볍고 빠름. 오인식이 잦은 편 (v1 기본값)", 0.33),
    ModelInfo("small.en", "english", "Small (영어)", "244M",
              "가벼움. distil-small보다 조금 정확", 0.48),
    ModelInfo("distil-large-v3.5", "english", "Distil Large v3.5 (영어)", "756M",
              "영어 고정밀. large급 정확도에 빠른 속도", 1.5, recommended=True),
    ModelInfo("small", "multilingual", "Small (다국어)", "244M",
              "가벼운 다국어. 짧은 문장의 언어 판별은 약함", 0.48),
    ModelInfo("medium", "multilingual", "Medium (다국어)", "769M",
              "정확도와 무게의 중간", 1.5),
    ModelInfo("large-v3-turbo", "multilingual", "Large v3 Turbo (다국어)", "809M",
              "다국어 정확도·속도 균형. 문장부호를 자주 생략", 1.6, recommended=True),
    ModelInfo("large-v3", "multilingual", "Large v3 (다국어)", "1550M",
              "최고 정확도. 가장 무겁고 느림", 3.1),
]
_BY_ID = {m.id: m for m in CATALOG}


def load_stats():
    """Attach measured VRAM/speed (tools/measure_models.py output) to the catalog."""
    try:
        with open(paths.MODEL_STATS_PATH, encoding="utf-8") as f:
            stats = json.load(f)
    except (OSError, ValueError):
        return {}
    for m in CATALOG:
        fp16 = stats.get("models", {}).get(m.id, {}).get("float16", {})
        m.vram_mb = fp16.get("vram_mb")
        m.rtf = fp16.get("rtf")
    return stats


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


# ---------------- cloud speech recognition (Groq) ----------------

GROQ_URL = "https://api.groq.com/openai/v1"
CLOUD_MODELS = {
    "whisper-large-v3": "정확도 우선 (추천)",
    "whisper-large-v3-turbo": "조금 더 빠름, 문장부호를 자주 빼먹음",
}
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
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


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
            raise CloudQuotaError(f"Groq 무료 한도에 도달했습니다 ({int(wait)}초 후 재개)", wait)
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
        self._notice = self._changed = None
        self._cloud_paused_until = 0.0
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.model is not None or self.cloud is not None

    def describe(self):
        if self.cloud is not None:
            left = f" · 오늘 남은 요청 {self.cloud.remaining:,}" if self.cloud.remaining is not None else ""
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
        self._notice, self._changed = notice, changed
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
        with self._lock:
            self._gen += 1  # a detector still loading for a previous local model is dropped
            self.model = self.gate = None  # frees any local model's VRAM
            self.gate_state = "off"
            self._gate_done.set()
            self.cloud = client
            self.model_id, self.device, self.compute_type = client.model, "cloud", ""
            self._cloud_paused_until = 0.0
            self.cfg = dict(cfg)
        log.info("STT ready: %s", self.describe())
        return []

    def _transcribe_cloud(self, cloud, audio, prev_text, cfg):
        t0 = time.perf_counter()
        result = SttResult(audio_sec=len(audio) / 16000, language="")
        wait = self._cloud_paused_until - time.monotonic()
        if wait > 0:
            result.skipped, result.skip_note = "cloud_paused", f"Groq 한도 대기 중 ({int(wait)}초 남음)"
            return result
        # Groq picks the language itself; Korean is judged from the transcript's script.
        language = None
        prompt = prev_text[-200:] if cfg.get("use_context", True) and prev_text else None
        try:
            reply = cloud.transcribe(_normalize(audio), language, prompt)
        except CloudQuotaError as e:
            self._cloud_paused_until = time.monotonic() + e.retry_after
            result.skipped, result.skip_note = "cloud_quota", str(e)
            return result
        except CloudTransientError as e:
            result.skipped, result.skip_note = "cloud_error", str(e)
            return result
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
        result.language = language or _WHISPER_LANG_NAMES.get((reply.get("language") or "").lower(), "")
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
