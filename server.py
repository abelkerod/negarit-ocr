"""
Reads the text off a receipt screenshot. RapidOCR (PP-OCR models) on OpenVINO,
falling back to ONNX Runtime. No GPU needed.

Start: uv run uvicorn server:app --port 8765
"""
import io
import os
import time

import numpy as np
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from PIL import Image, ImageOps
from rapidocr import RapidOCR, EngineType

MAX_SIDE = int(os.environ.get("OCR_MAX_SIDE", "1400"))  # screenshot text is large; 1400 keeps SMS fonts crisp
SECRET = os.environ.get("OCR_SECRET")

# Threads: the box's cores split across the uvicorn workers, so workers do not
# oversubscribe each other. Override either with OCR_THREADS / OCR_WORKERS.
_workers = max(1, int(os.environ.get("OCR_WORKERS", "2")))
_threads = int(os.environ.get("OCR_THREADS", str(max(1, (os.cpu_count() or 2) // _workers))))
# OpenVINO beat onnxruntime on our Intel test box: detection 3.2x faster (1218ms ->
# 375ms), same text on every sample. Set OCR_ENGINE=onnxruntime to fall back.
_engine = EngineType.OPENVINO if os.environ.get("OCR_ENGINE", "openvino") == "openvino" else EngineType.ONNXRUNTIME
ocr = RapidOCR(params={
    "Global.log_level": "warning",
    "Global.use_cls": False,  # screenshots are never upside down
    "Det.engine_type": _engine,
    "Rec.engine_type": _engine,
    "Det.intra_op_num_threads": _threads,
    "Rec.intra_op_num_threads": _threads,
    "EngineConfig.openvino.inference_num_threads": _threads,
})
app = FastAPI()


def to_array(data: bytes) -> np.ndarray:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    scale = MAX_SIDE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return np.asarray(img)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr")
async def run(file: UploadFile = File(...), x_ocr_secret: str | None = Header(default=None)):
    if SECRET and x_ocr_secret != SECRET:
        raise HTTPException(401)
    started = time.perf_counter()
    result = ocr(to_array(await file.read()))
    lines = [
        {"text": t, "confidence": round(float(s), 3)}
        for t, s in zip(result.txts or [], result.scores or [])
    ]
    return {
        "text": "\n".join(l["text"] for l in lines),
        "lines": lines,
        "ms": round((time.perf_counter() - started) * 1000),
    }
