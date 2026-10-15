"""Run a WAV file through the full pipeline without the UI or a game.

    EN2KR4GAMING.exe --selftest speech.wav [--out report.txt] [--model distil-small.en]

Uses the user's real settings (API keys included) but never touches audio
devices. Exit code 0 when at least one line was recognised.
"""
import argparse
import logging
import os
import sys
import time
import wave

import numpy as np

from . import paths
from .pipeline import Pipeline, PipelineEvents


def load_wav(path):
    with wave.open(path) as w:
        if w.getframerate() != 16000 or w.getsampwidth() != 2:
            raise ValueError("16 kHz 16-bit PCM WAV가 필요합니다")
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            audio = audio.reshape(-1, w.getnchannels()).mean(axis=1)
    return audio


def run(settings, wav_path, model=None, mode=None, timeout=900):
    lines, translations, notes, status = [], [], [], {}
    events = PipelineEvents(
        status=lambda comp, state, msg: status.__setitem__(comp, (state, msg)),
        stt=lines.append,
        translation=translations.append,
        notice=notes.append,
    )
    settings.set("audio.mode", "none")
    if model:
        settings.set("stt.model", model)
    if mode:
        settings.set("stt.mode", mode)
    p = Pipeline(settings, events)
    p.start()
    try:
        t0 = time.monotonic()
        while not p.engine.ready and time.monotonic() - t0 < timeout:
            if status.get("stt", ("",))[0] == "error":
                break
            time.sleep(0.2)
        if not p.engine.ready:
            return {"ok": False, "status": status, "lines": lines, "translations": translations, "notes": notes}
        p.engine.wait_gate(timeout=300)  # the Korean detector loads in the background
        audio = load_wav(wav_path)
        p.feed_file_audio(np.concatenate([np.zeros(8000, np.float32), audio, np.zeros(32000, np.float32)]))
        sent = lambda: [l for l in lines if l.line_id is not None]
        deadline = time.monotonic() + 60
        idle_since = time.monotonic()
        last_counts = (0, 0)
        while time.monotonic() < deadline:
            counts = (len(lines), len(translations))
            if counts != last_counts:
                last_counts, idle_since = counts, time.monotonic()
            untranslated = {l.line_id for l in sent()} - {t.line_id for t in translations}
            busy = not p._audio_q.empty() or not p._seg_q.empty() or bool(untranslated)
            if not busy and time.monotonic() - idle_since > 3.0:
                break
            time.sleep(0.1)
    finally:
        p.stop()
    return {"ok": bool(lines), "status": status, "lines": lines, "translations": translations, "notes": notes,
            "engine": p.engine.describe()}


def format_report(r):
    out = [f"engine: {r.get('engine', '-')}", f"status: {r['status']}"]
    for l in r["lines"]:
        tag = f"#{l.line_id}" if l.line_id is not None else "--"
        out.append(f"STT {tag} [{l.language}] {l.text!r} note={l.note} latency={l.latency:.2f}s")
    for t in r["translations"]:
        out.append(f"TR  #{t.line_id} {t.text!r} provider={t.provider} error={t.error} cached={t.cached}")
    for n in r["notes"]:
        out.append(f"NOTE {n}")
    out.append("RESULT " + ("OK" if r["ok"] else "FAIL"))
    return "\n".join(out)


def main(argv):
    from . import log
    from .settings import Settings

    ap = argparse.ArgumentParser(prog="EN2KR4GAMING --selftest")
    ap.add_argument("--selftest", required=True, metavar="WAV")
    ap.add_argument("--out")
    ap.add_argument("--model")
    ap.add_argument("--mode", choices=["english", "fixed", "auto"])
    # The windowed exe has no console: an uncaught error would leave PyInstaller's hidden error
    # dialog waiting forever, so every failure is written to the report instead.
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    if not args.out and sys.stdout is None:
        args.out = os.path.join(paths.app_dir(), "selftest.txt")
    log.setup()
    try:
        if not os.path.isfile(args.selftest):
            raise FileNotFoundError(f"WAV 파일이 없습니다: {args.selftest}")
        report = format_report(run(Settings().load(), args.selftest, args.model, args.mode))
    except Exception as e:
        logging.getLogger(__name__).exception("selftest failed")
        report = f"ERROR {type(e).__name__}: {e}\nRESULT FAIL"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    else:
        print(report)
    return 0 if report.endswith("OK") else 1
