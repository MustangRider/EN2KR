"""Daily transcript files of translated lines, with a retention limit."""
import logging
import os
import threading
import time
from datetime import datetime

from . import paths

log = logging.getLogger(__name__)


class TranscriptLog:
    def __init__(self, enabled=True, retention_days=14):
        self.enabled = enabled
        self.retention_days = retention_days
        self._lock = threading.Lock()

    def write(self, lang, source, translated):
        if not self.enabled:
            return
        now = datetime.now()
        path = os.path.join(paths.transcript_dir(), f"{now:%Y-%m-%d}.txt")
        try:
            with self._lock, open(path, "a", encoding="utf-8") as f:
                f.write(f"[{now:%H:%M:%S}] ({lang}) {source} -> {translated}\n")
        except OSError as e:
            log.warning("Transcript write failed: %s", e)

    def cleanup(self):
        if self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        try:
            for entry in os.scandir(paths.transcript_dir()):
                if entry.is_file() and entry.name.endswith(".txt") and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
        except OSError as e:
            log.warning("Transcript cleanup failed: %s", e)
