# negarit-ocr

Reads the text off a payment receipt screenshot so Negarit can find the
transaction reference in it. PP-OCR models on RapidOCR, CPU only, no GPU.

Negarit calls this first and falls back to Odit when it comes back empty.

## API

`POST /ocr` — the image as multipart `file`, the shared secret in the
`x-ocr-secret` header. Answers:

    { "text": "...", "lines": [{ "text": "...", "confidence": 0.98 }], "ms": 412 }

`GET /health` — no auth, answers `{"ok": true}`.

The secret is the only thing standing between this and the open internet, so
set `OCR_SECRET` to something long and keep it out of git.

## Environment

| Variable | Default | What it does |
|---|---|---|
| `OCR_SECRET` | unset | Required in `x-ocr-secret`. Unset means no auth at all. |
| `OCR_WORKERS` | `2` | Uvicorn workers. Each loads its own models (~500MB). |
| `OCR_THREADS` | cores ÷ workers | Inference threads per worker. |
| `OCR_MAX_SIDE` | `1400` | Longest image side before OCR. Lower misreads SMS text. |
| `OCR_ENGINE` | `openvino` | `onnxruntime` to fall back. |

## Run locally

    uv sync
    OCR_SECRET=dev uv run uvicorn server:app --port 8765

Or with Docker: `OCR_SECRET=dev docker compose up --build`.

## Deploy on Coolify

Add a resource, pick **Public Git Repository**, point it at this repo. Coolify
finds the Dockerfile on its own. Then set:

- Port: `8765`
- `OCR_SECRET`: a long random value (`openssl rand -hex 32`)
- `OCR_WORKERS`: leave at 2, raise it if the box has cores to spare

Coolify terminates TLS at its own proxy, so this container never needs a
certificate and should never be published on a port of its own.

Point Negarit at the result with `OCR_URLS`. It is comma-separated and tried in
order, so a second box is one env edit.

## Performance

OpenVINO is the default because it beat ONNX Runtime badly on detection: 1218ms
down to 375ms on a 2-core i7-7500U, and the same reference came out of all
nine regression samples. Recognition barely moved. It costs memory, around 800MB per worker
against 215MB, which is why the compose file allows 3GB.

Whole-image latency on that 2-core box was about 1.4s through the API. A server CPU should do
much better.
