"""Whisper hallucination filtering.

Permanent list: exact phrases the user blocked (case/punctuation-insensitive).
Auto-ban: a short phrase that keeps coming back *with low confidence* is
banned for this session only. v1 banned any short phrase repeated 5 times and
saved it forever, which also silenced real callouts like "Behind you!".
"""
import collections
import re
import threading
import time

_TRAIL = re.compile(r"[\s.,!?…~\-]+$")
_SPACES = re.compile(r"\s+")

# Phrases Whisper invents from silence/music (YouTube subtitle artefacts).
CONTAINS_PATTERNS = ("thank you for watching", "thanks for watching", "subtitles by", "please subscribe",
                     "like and subscribe", "♪")

AUTO_BAN_COUNT = 4
AUTO_BAN_WINDOW = 600.0


def normalize(text):
    return _SPACES.sub(" ", _TRAIL.sub("", text.strip().lower()))


class HallucinationFilter:
    def __init__(self, blocked=(), on_auto_ban=None):
        self.on_auto_ban = on_auto_ban or (lambda text: None)
        self._lock = threading.Lock()
        self._blocked = set()
        self._session = {}                      # normalized -> original text
        self._history = collections.defaultdict(collections.deque)
        self.set_blocked(blocked)

    def set_blocked(self, phrases):
        with self._lock:
            self._blocked = {normalize(p) for p in phrases if normalize(p)}
            for n in list(self._session):
                if n in self._blocked:
                    del self._session[n]

    @property
    def session_bans(self):
        with self._lock:
            return list(self._session.values())

    def unban(self, text):
        with self._lock:
            self._session.pop(normalize(text), None)
            self._history.pop(normalize(text), None)

    def check(self, text, avg_logprob=0.0, no_speech_prob=0.0, now=None):
        """Return None if the text may pass, else a short reason string."""
        n = normalize(text)
        if not n:
            return "빈 결과"
        with self._lock:
            if n in self._blocked:
                return "차단 목록"
            if n in self._session:
                return "자동 차단(이번 실행)"
        if any(p in n for p in CONTAINS_PATTERNS):
            return "자막형 환청"
        if len(n.split()) > 3:
            return None
        suspicious = no_speech_prob > 0.3 or avg_logprob < -0.7
        if not suspicious:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            hist = self._history[n]
            hist.append(now)
            while hist and now - hist[0] > AUTO_BAN_WINDOW:
                hist.popleft()
            if len(hist) < AUTO_BAN_COUNT:
                return None
            self._session[n] = text.strip()
            del self._history[n]
        self.on_auto_ban(text.strip())
        return "자동 차단(이번 실행)"
