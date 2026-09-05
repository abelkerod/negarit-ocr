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
import hashlib
import json
import os
import struct
import subprocess
import time
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

from extract import PROVIDERS, extract

SECRET = os.environ.get("OCR_SECRET")
# psm 6 (one uniform block) or 11 (sparse) both read every sample; the default
# psm 3 found the reference in only three of nine, so this is not a knob to
# leave alone.
PSM = os.environ.get("OCR_PSM", "6")
LANG = os.environ.get("OCR_LANG", "eng")
TIMEOUT_S = float(os.environ.get("OCR_TIMEOUT", "30"))
# Empty disables it, for a caller reading something that is not a reference.
WHITELIST = os.environ.get(
    "OCR_WHITELIST",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:/.-_?=&+ ",
)
# A small file can still be an enormous picture, and every pixel is a worker
# held. 30MP is far above any phone screenshot and far below anything that
# would tie this box up.
MAX_PIXELS = int(os.environ.get("OCR_MAX_PIXELS", str(30_000_000)))
# Where every request and every image it carried is kept. Unset means nothing
# is written, which is how the image ships: an archive of receipts is a thing
# you opt into on purpose, not something a default turns on.
ARCHIVE_DIR = os.environ.get("OCR_ARCHIVE_DIR", "")
# A full disk stops the box reading anything at all, so the archive has a
# ceiling. Oldest images go first; the request log is never pruned.
ARCHIVE_MAX_MB = int(os.environ.get("OCR_ARCHIVE_MAX_MB", "2048"))

SUFFIXES = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8", ".jpg"), (b"RIFF", ".webp"))

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
        # The whitelist prunes the beam of characters a reference can never
        # contain: lowercase l, o, the pipe, the full stop. Measured +5 points
        # on 80 telebirr screenshots. It cannot choose between O and 0, since
        # both are legal; the day-of-year check in extract.py does that.
        ["tesseract", "stdin", "stdout", "--psm", PSM, "-l", LANG,
         "-c", "tessedit_char_whitelist=" + WHITELIST, "tsv"],
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


def suffix_for(data: bytes) -> str:
    for magic, suffix in SUFFIXES:
        if data.startswith(magic):
            return suffix
    return ".bin"


def prune(images: str) -> None:
    """Drop the oldest images once the archive passes its ceiling."""
    entries = []
    total = 0
    for name in os.listdir(images):
        path = os.path.join(images, name)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size
    ceiling = ARCHIVE_MAX_MB * 1024 * 1024
    for _, size, path in sorted(entries):
        if total <= ceiling:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


def archive(data: bytes, entry: dict) -> None:
    """Keep the request and the image it carried.

    Content-addressed, so a buyer's retry of the same screenshot costs one
    file and two log lines rather than two files. Never raises: an archive
    that cannot be written is not a reason to fail a read the buyer is
    waiting on.
    """
    if not ARCHIVE_DIR:
        return
    try:
        images = os.path.join(ARCHIVE_DIR, "images")
        os.makedirs(images, exist_ok=True)
        name = hashlib.sha256(data).hexdigest()[:16] + suffix_for(data)
        path = os.path.join(images, name)
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(data)
            prune(images)
        entry["file"] = name
        with open(os.path.join(ARCHIVE_DIR, "requests.jsonl"), "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as error:  # noqa: BLE001 - archiving must never fail a read
        print(f"[ocr] archive failed: {type(error).__name__}: {error}", flush=True)


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
    elapsed = round((time.perf_counter() - started) * 1000)
    archive(data, {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider,
        "engine": engine,
        "ms": elapsed,
        "bytes": len(data),
        "px": list(size) if size else None,
        "reference": found["reference"],
        "tokens": found["tokens"],
        "lines": len(lines),
    })
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
        "ms": elapsed,
    }
