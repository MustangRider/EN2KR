"""Speech-to-text with faster-whisper: model catalog and engine."""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import gpu, paths

log = logging.getLogger(__name__)

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
    if is_downloaded(model_id) or progress is None:
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
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    latency: float = 0.0
    audio_sec: float = 0.0
    rejected: list = field(default_factory=list)  # low-confidence segment texts


class SttEngine:
    MAX_NO_SPEECH = 0.6
    MIN_AVG_LOGPROB = -1.0
    KOREAN_SKIP_MIN = 0.85   # raw Whisper probability needed before discarding speech as Korean

    def __init__(self):
        self.model = None
        self.model_id = None
        self.device = None
        self.compute_type = None
        self.int8_unsupported = False
        self.cfg = {}
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.model is not None

    def describe(self):
        if not self.model:
            return "로드 안 됨"
        return f"{self.model_id} · {self.device.upper()} · {self.compute_type}"

    def load(self, cfg, progress=None):
        """Load per cfg (the 'stt' settings section). Returns a list of warnings."""
        from faster_whisper import WhisperModel

        warnings = []
        cuda = cfg.get("device", "auto") != "cpu" and gpu.cuda_device_count() > 0
        model_id = resolve_model_id(cfg, cuda)
        path = download(model_id, progress)
        attempts = []
        if cuda:
            if cfg.get("int8"):
                attempts.append(("cuda", "int8_float16"))
            attempts.append(("cuda", "float16"))
        attempts.append(("cpu", "int8"))

        last_error = None
        for device, compute in attempts:
            try:
                model = WhisperModel(path, device=device, compute_type=compute,
                                     cpu_threads=min(4, os.cpu_count() or 4))
                # Some failures (e.g. int8 GEMM on new GPUs) only surface on first use.
                list(model.transcribe(np.zeros(16000, np.float32), language="en", beam_size=1)[0])
            except Exception as e:
                last_error = e
                log.warning("Loading %s on %s/%s failed: %s", model_id, device, compute, e)
                if compute.startswith("int8") and device == "cuda":
                    self.int8_unsupported = True
                    warnings.append("이 GPU는 VRAM 절약 모드(int8)를 지원하지 않아 float16으로 실행합니다.")
                elif device == "cuda":
                    warnings.append(f"GPU로 모델을 불러오지 못해 CPU로 실행합니다. ({e})")
                continue
            with self._lock:
                self.model, self.model_id, self.device, self.compute_type = model, model_id, device, compute
                self.cfg = dict(cfg)
            log.info("STT ready: %s", self.describe())
            return warnings
        raise RuntimeError(f"음성 인식 모델을 불러오지 못했습니다: {last_error}")

    def update_options(self, cfg):
        """Apply options that don't need a reload (language mode, context...)."""
        with self._lock:
            self.cfg = dict(cfg)

    def transcribe(self, audio, prev_text=None):
        with self._lock:
            model, cfg, model_id = self.model, dict(self.cfg), self.model_id
        if model is None:
            raise RuntimeError("STT model not loaded")
        t0 = time.perf_counter()
        audio = _normalize(audio)
        info_kind = _BY_ID.get(model_id).kind if model_id in _BY_ID else "multilingual"
        mode = cfg.get("mode", "english")
        if info_kind == "english":
            mode = "english"
        lang = "en" if mode == "english" else cfg.get("language", "en")
        opts = dict(beam_size=int(cfg.get("beam_size", 5)), condition_on_previous_text=False,
                    without_timestamps=True, vad_filter=False)
        if cfg.get("use_context", True) and prev_text:
            # faster-whisper resets context on every call; initial_prompt carries it over.
            opts["initial_prompt"] = prev_text[-200:]

        result = SttResult(audio_sec=len(audio) / 16000, language=lang)
        if mode == "auto":
            segments, info = model.transcribe(audio, language=None, **opts)
            allowed = cfg.get("auto_languages", ["en"])
            fallback = cfg.get("language", "en")
            lang, conf = choose_language(info.all_language_probs, allowed, fallback=fallback)
            if lang == "ko" and cfg.get("skip_korean", True):
                raw_ko = dict(info.all_language_probs or []).get("ko", 0.0)
                if raw_ko >= self.KOREAN_SKIP_MIN:
                    result.language, result.language_conf, result.skipped = "ko", raw_ko, "korean"
                    result.latency = time.perf_counter() - t0
                    return result
                # Korean-accented English is often scored as "ko": don't drop it, pick the next language.
                lang, conf = choose_language(info.all_language_probs, [l for l in allowed if l != "ko"],
                                             fallback=fallback if fallback != "ko" else "en", min_confidence=0.0)
            result.language, result.language_conf = lang, conf
            if lang != info.language:
                segments, info = model.transcribe(audio, language=lang, **opts)
        else:
            segments, info = model.transcribe(audio, language=lang, **opts)

        texts, logprobs, nospeech = [], [], []
        for s in segments:
            txt = s.text.strip()
            if not txt:
                continue
            if s.no_speech_prob < self.MAX_NO_SPEECH and s.avg_logprob > self.MIN_AVG_LOGPROB:
                texts.append(txt)
                logprobs.append(s.avg_logprob)
                nospeech.append(s.no_speech_prob)
            else:
                result.rejected.append(txt)
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
