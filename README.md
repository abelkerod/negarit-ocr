# negarit-ocr

Reads the reference off a payment receipt screenshot. QR first, Tesseract
after. CPU only, no SIMD required.

Negarit calls this first and falls back to Odit when it comes back empty.

## API

`POST /ocr` — the image as multipart `file`, the shared secret in the
`x-ocr-secret` header, and optionally `provider` (`cbe` or `telebirr`).

The provider is a parameter, never a guess. Working out which bank a
screenshot belongs to is a harder problem than reading it, and the caller
already knows which one the buyer picked. Without it the box still answers
text and barcodes, just no reference. An unknown provider is a 400.

Answers:

    { "text": "...", "lines": [{ "text": "...", "confidence": 0.98 }],
      "qr": ["https://mbreciept.cbe.com.et/v2-..."], "engine": "qr",
      "provider": "cbe", "reference": "https://mbreciept.cbe.com.et/v2-...",
      "links": [...], "tokens": ["FT26241X7KQ3"], "legacy": [], "ms": 74 }

`reference` is the one worth submitting: the receipt link when there is one,
otherwise the bare token for providers that accept it. CBE does not — its FT
number keys a retired endpoint — so a CBE receipt with no readable link
answers `reference: null` and names the token in `tokens` anyway.

`candidates` are the readings the check digit accepts, best first. Usually one.
Where there are two, the token cannot say which was printed, so ask the bank
about each in turn rather than guessing.

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
| `OCR_ARCHIVE_DIR` | unset | Where to keep every request and the image it carried. Unset writes nothing. |
| `OCR_ARCHIVE_MAX_MB` | `2048` | Ceiling on the images directory. Oldest go first; the request log is never pruned. |
| `OMP_THREAD_LIMIT` | `1` | Tesseract's own threads. Raising it slowed us down. |

`OCR_PSM` is not a knob to leave alone. On our nine regression receipts, psm 6
and psm 11 both found every reference; the Tesseract default of psm 3 found
three of nine.

## Tests

    python3 test_extract.py

The grammar here mirrors Negarit's `shared/extract-reference.ts`. They are two
implementations of one thing and will drift if only one is changed; those
fixtures are the cheapest place to notice.

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

## Keeping what came in

Set `OCR_ARCHIVE_DIR` and every request is appended to `requests.jsonl` there,
with the image beside it under `images/`:

    {"at":"2026-09-05T19:22:11+00:00","provider":"telebirr","engine":"tesseract",
     "ms":1710,"bytes":348843,"px":[1080,2316],"reference":"DHO742I76F",
     "tokens":["DHO742I76F"],"lines":18,"file":"9c1f2a77b3e40d51.jpg"}

Images are named by their own hash, so a buyer retrying the same screenshot
costs one file and two log lines. Archiving never fails a read: a directory it
cannot write to is logged and ignored.

These are real receipts, carrying names, part of an account number and a
balance. Point `OCR_ARCHIVE_DIR` at a volume you are willing to hold that on,
and leave it unset anywhere you are not.

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
