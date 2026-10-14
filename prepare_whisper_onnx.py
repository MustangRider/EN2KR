from pathlib import Path
import traceback

try:
    from optimum.onnxruntime import ORTModelForSpeechSeq2Seq
    from transformers import WhisperProcessor

    model_id = "distil-whisper/distil-small.en"
    out_dir = Path("models/whisper_onnx")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[ONNX] export start")
    model = ORTModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        export=True,
        provider="CPUExecutionProvider",
    )
    processor = WhisperProcessor.from_pretrained(model_id)

    model.save_pretrained(out_dir)
    processor.save_pretrained(out_dir)
    print(f"[ONNX] done: {out_dir}")
except Exception as e:
    print("[ONNX] error:", e)
    traceback.print_exc()
    raise SystemExit(1)
