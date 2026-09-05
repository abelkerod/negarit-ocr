"""
Reads the text off a receipt screenshot so Negarit can find the transaction
reference in it. Tesseract 5, CPU only.

Tesseract is here because it needs no SIMD: it ships a plain C++ fallback, so
it runs on old or masked CPUs where numpy, ONNX Runtime and OpenVINO all
refuse to load. On our nine regression receipts it pulled exactly the same
reference as the RapidOCR box it replaced, at about half the time.

Start: uv run uvicorn server:app --port 8765
"""
import os
import subprocess
import time

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

SECRET = os.environ.get("OCR_SECRET")
# psm 6 (one uniform block) or 11 (sparse) both read every sample; the default
# psm 3 found the reference in only three of nine, so this is not a knob to
# leave alone.
PSM = os.environ.get("OCR_PSM", "6")
LANG = os.environ.get("OCR_LANG", "eng")
TIMEOUT_S = float(os.environ.get("OCR_TIMEOUT", "30"))

app = FastAPI()


def read(data: bytes) -> list[dict]:
    """Tesseract's TSV, folded back into one entry per line of text."""
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", PSM, "-l", LANG, "tsv"],
        input=data, capture_output=True, timeout=TIMEOUT_S,
    )
    if done.returncode != 0:
        # Tesseract only exits non-zero when it cannot read what it was given,
        # so this is the caller's problem, not an outage. It matters which:
        # Negarit retries 5xx as a transient failure, and would burn a buyer's
        # whole retry ladder on an image that can never be read. The stderr is
        # logged rather than returned because leptonica echoes the bytes it
        # choked on straight back into the message.
        print(f"[ocr] unreadable input: {done.stderr.decode('utf-8', 'replace')[:300]}", flush=True)
        raise HTTPException(400, "could not read that image")

    rows = done.stdout.decode("utf-8", "replace").splitlines()
    lines: dict[tuple, list[tuple[str, float]]] = {}
    for row in rows[1:]:  # first row is the header
        col = row.split("\t")
        if len(col) < 12 or col[0] != "5":  # level 5 is a word; the rest are boxes around them
            continue
        word = col[11].strip()
        if not word:
            continue
        lines.setdefault((col[2], col[3], col[4]), []).append((word, float(col[10])))

    out = []
    for words in lines.values():
        # Tesseract scores 0-100 per word; the old box answered 0-1 per line,
        # and callers still read it that way.
        confidence = sum(score for _, score in words) / len(words) / 100
        out.append({"text": " ".join(word for word, _ in words), "confidence": round(confidence, 3)})
    return out


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr")
async def run(file: UploadFile = File(...), x_ocr_secret: str | None = Header(default=None)):
    if SECRET and x_ocr_secret != SECRET:
        raise HTTPException(401)
    started = time.perf_counter()
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    try:
        lines = read(data)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "tesseract timed out")
    return {
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "ms": round((time.perf_counter() - started) * 1000),
    }
