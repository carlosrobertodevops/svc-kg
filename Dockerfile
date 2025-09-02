FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (opcional) — pyvis roda em puro JS no browser, sem libs nativas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tini ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8080

ENV PORT=8080 \
    WORKERS=2 \
    LOG_LEVEL=info

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "-lc", "exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WORKERS:-2} -b 0.0.0.0:${PORT:-8080} app:app --log-level ${LOG_LEVEL:-info}"]
