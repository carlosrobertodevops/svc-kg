FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# utilitarios
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia app e docs (serão sobrepostos por volumes no compose, se existirem)
COPY app.py ./app.py
COPY docs ./docs
COPY static ./static
COPY assets ./assets

EXPOSE 8080

# Usa variáveis de ambiente do container (WORKERS/PORT/LOG_LEVEL)
CMD ["/bin/sh", "-lc", "exec gunicorn -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:${PORT:-8080} --workers ${WORKERS:-2} --log-level ${LOG_LEVEL:-info} --timeout 90"]
