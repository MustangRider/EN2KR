"""Background translation with provider fallback, caching and back-pressure.

- submit() never blocks (v1 froze the UI when the queue filled up offline).
- Each recognised line is translated once; a newer job for the same line
  supersedes a queued older one (no duplicate API calls).
- Providers are tried in the user's order; one that fails is put on a
  cooldown matching the failure (bad key, quota used up, network blip).
"""
import collections
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import providers as P
from .text import Glossary, Slang

log = logging.getLogger(__name__)

COOLDOWN_SEC = {"auth": 600, "quota": 3600, "transient": 15}


@dataclass
class Job:
    line_id: int
    text: str
    lang: str = "en"
    replace: bool = False   # update the existing overlay line instead of adding one


@dataclass
class Result:
    line_id: int
    source: str
    text: str = ""
    provider: Optional[str] = None
    error: Optional[str] = None
    latency: float = 0.0
    cached: bool = False
    replace: bool = False
    lang: str = "en"


class TranslationService:
    MAX_PENDING = 32

    def __init__(self, on_result, on_notice=None):
        self.on_result = on_result
        self.on_notice = on_notice or (lambda msg: None)
        self._jobs = collections.deque()
        self._cond = threading.Condition()
        self._running = False
        self._thread = None
        self._providers = []
        self._glossary = Glossary({})
        self._slang = Slang({})
        self._use_glossary = self._use_slang = True
        self._cache = collections.OrderedDict()
        self._cache_size = 500
        self._cooldown = {}
        self._cfg_lock = threading.Lock()

    # ---------- configuration ----------
    def configure(self, settings):
        """settings: full settings snapshot (dict)."""
        tr = settings.get("translate", {})
        built = []
        for pid in tr.get("order", []):
            if pid not in P.PROVIDERS:
                continue
            prov = P.create(pid, tr.get("providers", {}).get(pid, {}))
            if prov.configured:
                built.append(prov)
        with self._cfg_lock:
            changed = [p.id for p in built]
            self._cooldown = {k: v for k, v in self._cooldown.items() if k not in changed}
            self._providers = built
            self._glossary = Glossary(settings.get("glossary", {}))
            self._slang = Slang(settings.get("slang", {}))
            self._use_glossary = bool(tr.get("use_glossary", True))
            self._use_slang = bool(tr.get("use_slang", True))
            self._cache_size = int(tr.get("cache_size", 500))
            self._cache.clear()

    @property
    def provider_labels(self):
        with self._cfg_lock:
            return [p.label for p in self._providers]

    @property
    def configured(self):
        with self._cfg_lock:
            return bool(self._providers)

    # ---------- queue ----------
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, name="translate", daemon=True)
        self._thread.start()

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(2)

    def submit(self, job):
        with self._cond:
            if len(self._jobs) >= self.MAX_PENDING:
                dropped = self._jobs.popleft()
                log.warning("Translation backlog full; dropped line %s", dropped.line_id)
            self._jobs.append(job)
            self._cond.notify()

    def _next_job(self):
        with self._cond:
            while self._running and not self._jobs:
                self._cond.wait(0.5)
            if not self._running:
                return None
            job = self._jobs.popleft()
            if any(j.line_id == job.line_id for j in self._jobs):
                return False  # superseded by a newer version of the same line
            return job

    def _worker(self):
        while True:
            job = self._next_job()
            if job is None:
                return
            if job is False:
                continue
            try:
                res = self.translate_now(job.text, job.lang)
            except Exception as e:  # keep the worker alive no matter what
                log.exception("Translation crashed")
                res = Result(job.line_id, job.text, error=f"번역 처리 오류: {e}")
            res.line_id, res.replace, res.lang = job.line_id, job.replace, job.lang
            try:
                self.on_result(res)
            except Exception:
                log.exception("on_result callback failed")

    # ---------- translation ----------
    def translate_now(self, text, lang="en"):
        t0 = time.perf_counter()
        with self._cfg_lock:
            providers = list(self._providers)
            glossary, slang = self._glossary, self._slang
            use_glossary, use_slang = self._use_glossary, self._use_slang
        res = Result(0, text)
        if not providers:
            res.error = "번역 API가 설정되지 않았습니다"
            return res
        src = slang.expand(text) if use_slang and lang == "en" else text
        protected, used = glossary.protect(src) if use_glossary else (src, [])
        key = (lang, protected.strip().lower())
        with self._cfg_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
        if hit is not None:
            res.text, res.provider, res.cached = glossary.restore(hit[0], used), hit[1], True
            res.latency = time.perf_counter() - t0
            return res

        problems = []
        now = time.monotonic()
        for prov in providers:
            until = self._cooldown.get(prov.id, 0)
            if until > now:
                problems.append(f"{prov.label}: 대기 중({int(until - now)}초)")
                continue
            try:
                out = prov.translate(protected, lang)
            except P.AuthError as e:
                self._fail(prov, "auth", str(e), problems)
                continue
            except P.QuotaError as e:
                self._fail(prov, "quota", str(e), problems)
                continue
            except P.ProviderError as e:
                self._fail(prov, "transient", str(e), problems)
                continue
            except Exception as e:
                log.exception("%s crashed", prov.label)
                self._fail(prov, "transient", str(e), problems)
                continue
            with self._cfg_lock:
                self._cache[key] = (out, prov.label)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            res.text, res.provider = glossary.restore(out, used), prov.label
            res.latency = time.perf_counter() - t0
            return res
        res.error = "번역 실패 - " + "; ".join(problems)
        res.latency = time.perf_counter() - t0
        return res

    def _fail(self, prov, kind, message, problems):
        self._cooldown[prov.id] = time.monotonic() + COOLDOWN_SEC[kind]
        problems.append(f"{prov.label}: {message}")
        log.warning("%s failed (%s): %s", prov.label, kind, message)
        if kind != "transient":
            self.on_notice(f"{message} → {COOLDOWN_SEC[kind] // 60}분간 다음 API를 사용합니다")

    def usage(self):
        """{label: (used, limit)} for providers that report usage. Blocking - call off the UI thread."""
        with self._cfg_lock:
            providers = [p for p in self._providers if p.supports_usage]
        out = {}
        for p in providers:
            try:
                u = p.usage()
                if u:
                    out[p.label] = u
            except P.ProviderError as e:
                log.info("Usage query failed for %s: %s", p.label, e)
        return out
