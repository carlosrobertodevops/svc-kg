FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# utilitários para build e healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential \
  && rm -rf /var/lib/apt/lists/*

# 1) copie requirements e instale
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) copie o app
COPY app.py .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]
