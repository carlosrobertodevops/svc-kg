FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app
COPY app.py ./app.py
# estáticos opcionais (o app funciona mesmo sem eles)
RUN mkdir -p static assets docs tmp && \
    echo ":root{--bg:#0b0f19;--fg:#e5e7eb}html,body{height:100%;margin:0}body{background:#fff;color:#111;font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Helvetica,Arial}body[data-theme=\"dark\"]{background:var(--bg);color:var(--fg)}.kg-toolbar{position:fixed;top:.5rem;right:.5rem;z-index:10;display:flex;gap:.5rem;align-items:center;background:rgba(0,0,0,.6);color:#fff;border-radius:8px;padding:.35rem .5rem}.kg-toolbar h4{margin:0 .4rem 0 0;font-weight:600}.kg-toolbar button{cursor:pointer}.badge{background:#10b981;color:#fff;border-radius:9999px;padding:.1rem .4rem;font-size:12px}" > static/vis-style.css && \
    touch assets/.keep docs/.keep tmp/.keep

ENV PORT=8080 APP_ENV=production LOG_LEVEL=info

EXPOSE 8080

# 🚀 modo seguro: uvicorn simples (1 processo) — você pode subir --workers via CMD_ARGS
ENV CMD_ARGS="--host 0.0.0.0 --port 8080 --proxy-headers"
CMD exec uvicorn app:app $CMD_ARGS
