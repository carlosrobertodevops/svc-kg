FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# deps básicos
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app + assets
COPY app.py ./app.py
# estáticos (se faltar, o app usa CDN mesmo assim)
COPY static ./static
# diretórios vazios não quebram o build
RUN mkdir -p assets docs tmp && \
    touch assets/.keep docs/.keep tmp/.keep

EXPOSE 8080

ENV PORT=8080 APP_ENV=production WORKERS=2 LOG_LEVEL=info

# gunicorn com uvicorn workers
CMD exec gunicorn app:app \
    --bind 0.0.0.0:${PORT} \
    --workers ${WORKERS} \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
