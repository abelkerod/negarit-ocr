FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 OCR_MAX_SIDE=1400 OCR_WORKERS=2
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-install-project
COPY server.py .
# Pull model weights at build time so a cold container serves immediately.
RUN uv run python -c "from rapidocr import RapidOCR; RapidOCR()"
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/health')"
# Shell form so OCR_WORKERS expands; exec keeps uvicorn as PID 1 for signals.
CMD exec uv run uvicorn server:app --host 0.0.0.0 --port 8765 --workers ${OCR_WORKERS:-2}
