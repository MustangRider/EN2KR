"""Measure VRAM usage and speed of each catalog model on this PC.

Each (model, compute_type) pair runs in a fresh subprocess so the number
includes the CUDA context and cuBLAS/cuDNN workspaces - what the user
actually pays when the app loads that model.

Usage:
    python tools/measure_models.py path/to/speech.wav [--out en2kr/model_stats.json]
"""
import argparse
import json
import os
import subprocess
import sys
import time

MODELS = [
    "distil-small.en", "small.en", "distil-large-v3.5",
    "small", "medium", "large-v3-turbo", "large-v3",
]
COMPUTE_TYPES = ["float16"]


def add_cuda_dll_dirs():
    # Same CUDA libraries as the app (bundled cuBLAS/cuDNN, installed Toolkit ignored).
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from en2kr import gpu
    gpu.setup_cuda_dll_paths()


def gpu_used_mb():
    import ctypes

    class Mem(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

    nvml = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"))
    nvml.nvmlInit_v2()
    handle = ctypes.c_void_p()
    nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle))
    mem = Mem()
    nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
    return mem.used / 2**20


def child(model, compute_type, wav):
    import wave
    import numpy as np

    add_cuda_dll_dirs()
    base = gpu_used_mb()
    from faster_whisper import WhisperModel

    with wave.open(wav) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    m = WhisperModel(model, device="cuda", compute_type=compute_type)
    lang = "en"
    times = []
    for _ in range(3):  # first run warms up kernels
        t = time.perf_counter()
        segments, _ = m.transcribe(audio, language=lang, beam_size=5, without_timestamps=True)
        text = " ".join(s.text.strip() for s in segments)
        times.append(time.perf_counter() - t)
    peak = gpu_used_mb()
    print(json.dumps({
        "vram_mb": round(peak - base),
        "rtf": round(min(times[1:]) / (len(audio) / 16000), 3),
        "text": text,
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "en2kr", "model_stats.json"))
    ap.add_argument("--child", nargs=2, metavar=("MODEL", "COMPUTE_TYPE"))
    args = ap.parse_args()
    if args.child:
        child(*args.child, args.wav)
        return

    add_cuda_dll_dirs()
    from faster_whisper.utils import download_model
    for model in MODELS:
        print(f"[download] {model}", flush=True)
        download_model(model)

    gpu_name = ""
    try:
        gpu_name = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                  capture_output=True, text=True).stdout.strip()
    except OSError:
        pass
    stats = {"gpu": gpu_name, "measured": time.strftime("%Y-%m-%d"), "models": {}}
    for model in MODELS:
        stats["models"][model] = {}
        for ct in COMPUTE_TYPES:
            r = subprocess.run([sys.executable, __file__, args.wav, "--child", model, ct],
                               capture_output=True, text=True, encoding="utf-8")
            line = (r.stdout.strip().splitlines() or [""])[-1]
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                result = {"error": (r.stderr.strip().splitlines() or ["unknown"])[-1]}
            stats["models"][model][ct] = result
            print(f"[measure] {model:18s} {ct:13s} {result}", flush=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"saved {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
