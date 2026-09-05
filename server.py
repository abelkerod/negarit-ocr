"""
Reads the reference off a receipt screenshot. QR first, Tesseract after.

The provider is a parameter, never a guess. Working out which bank a
screenshot belongs to is a harder problem than reading it, and the caller
already knows which one the buyer picked. Without it the box still answers
text and barcodes, just no reference.

QR before OCR because CBE prints its live lookup token only in the QR: the
visible FT number keys an endpoint the bank retired, so reading it perfectly
still gets you nothing. The QR is checksummed, survives being scaled to 15%,
blurred 5px or saved at JPEG q30, and decodes in milliseconds. OCR is the
fallback for receipts with no QR at all, which is every telebirr SMS.

Tesseract is the OCR engine because it needs no SIMD: it ships a plain C++
fallback, so it runs on old or masked CPUs where numpy, ONNX Runtime and
OpenVINO all refuse to load.

Start: uv run uvicorn server:app --port 8765
"""
import os
import struct
import subprocess
import time

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

from extract import PROVIDERS, extract

SECRET = os.environ.get("OCR_SECRET")
# psm 6 (one uniform block) or 11 (sparse) both read every sample; the default
# psm 3 found the reference in only three of nine, so this is not a knob to
# leave alone.
PSM = os.environ.get("OCR_PSM", "6")
LANG = os.environ.get("OCR_LANG", "eng")
TIMEOUT_S = float(os.environ.get("OCR_TIMEOUT", "30"))
# A small file can still be an enormous picture, and every pixel is a worker
# held. 30MP is far above any phone screenshot and far below anything that
# would tie this box up.
MAX_PIXELS = int(os.environ.get("OCR_MAX_PIXELS", str(30_000_000)))

app = FastAPI()


def dimensions(data: bytes) -> tuple[int, int] | None:
    """Width and height straight out of the header, so a decode bomb never gets decoded."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":  # JPEG: walk the segments to a start-of-frame
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
        if data[12:16] == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
    return None


def read_qr(data: bytes) -> list[str]:
    """Whatever the barcodes in the image say. Exit 4 means there were none."""
    try:
        done = subprocess.run(
            ["zbarimg", "--raw", "-q", "-Sdisable", "-Sqrcode.enable", "/dev/stdin"],
            input=data, capture_output=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return []
    if done.returncode not in (0, 4):
        return []
    return [line.strip() for line in done.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def read_text(data: bytes) -> list[dict]:
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
async def run(
    file: UploadFile = File(...),
    provider: str | None = Form(default=None),
    x_ocr_secret: str | None = Header(default=None),
):
    if SECRET and x_ocr_secret != SECRET:
        raise HTTPException(401)
    started = time.perf_counter()
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    size = dimensions(data)
    if size and size[0] * size[1] > MAX_PIXELS:
        raise HTTPException(413, f"image is {size[0]}x{size[1]}, over the {MAX_PIXELS} pixel limit")

    provider = (provider or "").strip().lower() or None
    if provider and provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider {provider!r}, expected one of {sorted(PROVIDERS)}")

    qr = read_qr(data)
    lines = [{"text": q, "confidence": 1.0} for q in qr]
    # A QR that already answers the question makes OCR a second spent agreeing.
    # A QR that does not — an advert, a feedback form — must not silence it.
    if provider:
        answered = bool(extract("\n".join(qr), provider)["reference"])
    else:
        answered = any(q.startswith(("http://", "https://")) for q in qr)
    engine = "qr"
    if not answered:
        lines = lines + read_text(data)
        engine = "tesseract"

    text = "\n".join(line["text"] for line in lines)
    found = extract(text, provider) if provider else {"links": [], "tokens": [], "legacy": [], "reference": None}
    return {
        "text": text,
        "lines": lines,
        "qr": qr,
        "engine": engine,
        "provider": provider,
        "reference": found["reference"],
        "links": found["links"],
        "tokens": found["tokens"],
        "legacy": found["legacy"],
        "ms": round((time.perf_counter() - started) * 1000),
    }
