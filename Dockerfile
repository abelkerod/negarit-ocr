# Pinned by digest: this box exists because a host CPU surprised us, and a
# silent base image bump is the same class of surprise.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
# Tesseract's own OpenMP threading fights the uvicorn workers and is slower per
# image on small screenshots; parallelism comes from OCR_WORKERS instead.
ENV PYTHONUNBUFFERED=1 OCR_WORKERS=2 OMP_THREAD_LIMIT=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng zbar-tools \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-install-project
COPY server.py extract.py ./
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/health')"
# Shell form so OCR_WORKERS expands; exec keeps uvicorn as PID 1 for signals.
CMD exec uv run uvicorn server:app --host 0.0.0.0 --port 8765 --workers ${OCR_WORKERS:-2}
