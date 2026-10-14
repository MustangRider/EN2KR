"""Logging setup.

The release build has no console, so everything (including uncaught
exceptions in worker threads) goes to a rotating file in %APPDATA%.
"""
import logging
import logging.handlers
import os
import sys
import threading

from . import paths

log = logging.getLogger("en2kr")


def setup(level=logging.INFO):
    root = logging.getLogger()
    if getattr(root, "_en2kr_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        os.path.join(paths.log_dir(), "app.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    for noisy in ("urllib3", "httpx", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def excepthook(exc_type, exc, tb):
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        log.critical("Uncaught exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    root._en2kr_configured = True
