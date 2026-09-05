# negarit-ocr

Reads the reference off a payment receipt screenshot. QR first, Tesseract
after. CPU only, no SIMD required.

Negarit calls this first and falls back to Odit when it comes back empty.

## API

`POST /ocr` — the image as multipart `file`, the shared secret in the
`x-ocr-secret` header. Answers:

    { "text": "...", "lines": [{ "text": "...", "confidence": 0.98 }],
      "qr": ["https://mbreciept.cbe.com.et/v2-..."], "engine": "qr", "ms": 74 }

`engine` says which read answered: `qr` when a barcode held a link, otherwise
`tesseract`. Anything the QR carried is also the first line of `text`, so a
caller that only reads `text` needs no change.

A CBE app receipt answers from its QR in about 70ms. Everything else, telebirr
SMS included, goes through OCR at about 800ms.

`GET /health` — no auth, answers `{"ok": true}`.

The secret is the only thing standing between this and the open internet, so
set `OCR_SECRET` to something long and keep it out of git.

## Environment

| Variable | Default | What it does |
|---|---|---|
| `OCR_SECRET` | unset | Required in `x-ocr-secret`. Unset means no auth at all. |
| `OCR_WORKERS` | `2` | Uvicorn workers. Each is one Tesseract at a time. |
| `OCR_PSM` | `6` | Tesseract page segmentation mode. See the warning below. |
| `OCR_LANG` | `eng` | Tesseract language data. |
| `OCR_TIMEOUT` | `30` | Seconds before a read is abandoned. |
| `OCR_MAX_PIXELS` | `30000000` | Bigger images are refused with 413, read from the header so a decode bomb is never decoded. |
| `OMP_THREAD_LIMIT` | `1` | Tesseract's own threads. Raising it slowed us down. |

`OCR_PSM` is not a knob to leave alone. On our nine regression receipts, psm 6
and psm 11 both found every reference; the Tesseract default of psm 3 found
three of nine.

## Run locally

    uv sync
    OCR_SECRET=dev uv run uvicorn server:app --port 8765

Or with Docker: `OCR_SECRET=dev docker compose up --build`.

## Deploy on Coolify

Add a resource, pick **Public Git Repository**, point it at this repo, then set
**Build Pack to Dockerfile** — Coolify defaults to Railpack, which ignores the
Dockerfile and builds something else. Then set:

- Port: `8765`
- `OCR_SECRET`: a long random value (`openssl rand -hex 32`), with the
  "Build Variable" toggle **off** so it is not baked into the image
- `OCR_WORKERS`: leave at 2, raise it if the box has cores to spare

Coolify terminates TLS at its own proxy, so this container never needs a
certificate and should never be published on a port of its own.

Point Negarit at the result with `OCR_URLS`. It is comma-separated and tried in
order, so a second box is one env edit.

## Why QR first

CBE prints its live lookup token only inside the QR code. The visible FT number
keys an endpoint the bank retired, so reading it perfectly still gets you
nothing. The QR is checksummed rather than guessed, and it survived every abuse
we threw at it: scaled to 15%, blurred 5px, saved at JPEG q30, and re-shared at
40% scale and q40. It only fails once the code itself is cropped away or the
JPEG drops below about q30.

## Why Tesseract

This ran on RapidOCR (PP-OCR models on OpenVINO) until it met a KVM guest
exposing the "Common KVM processor" model, which has no SSE3, SSSE3, SSE4.1,
SSE4.2, POPCNT or AVX. NumPy 2.x, ONNX Runtime and OpenVINO all need those and
will not start without them. Tesseract ships a plain C++ fallback and needs
none, so it runs anywhere.

The swap cost nothing on our own receipts. Tesseract pulled the same reference
as RapidOCR on all nine regression samples, in about half the time, from an
image a fraction of the size.
