FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    WORKERS=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia app e assets estáticos
COPY app.py ./app.py
COPY static ./static
COPY assets ./assets
COPY docs ./docs

EXPOSE 8080

# usa gunicorn com workers uvicorn (respeita $WORKERS e $PORT)
CMD ["bash", "-lc", "exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WORKERS:-2} -b 0.0.0.0:${PORT:-8080} app:app --access-logfile - --error-logfile -"]