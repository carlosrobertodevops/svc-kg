# =============================================================================
# Arquivo: Dockerfile
# Versão: v1.7.20
# Objetivo: Construir a imagem do micro-serviço svc-kg com dependências
# Funções/métodos:
# - Configura ambiente Python 3.11 slim
# - Instala dependências do serviço (FastAPI, Gunicorn, PyVis, etc.)
# - Copia app.py, static e docs para o container
# - Baixa vendor local do vis-network (JS + CSS)
# - Define entrypoint com Gunicorn/Uvicorn
# =============================================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    "uvicorn[standard]==0.30.6" \
    "gunicorn==22.0.0" \
    "psycopg[binary]==3.2.1" \
    "psycopg_pool==3.2.1" \
    orjson==3.10.7 \
    httpx==0.27.2 \
    redis==5.0.7 \
    PyYAML==6.0.2 \
    networkx==3.3 \
    pyvis==0.3.2

WORKDIR /app

# Copia código e estáticos
COPY app.py /app/app.py
COPY static /app/static
COPY docs /app/docs

# Baixa assets locais do vis-network (sem CDN)
RUN mkdir -p /app/static/vendor \
 && curl -fsSL https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js -o /app/static/vendor/vis-network.min.js \
 && curl -fsSL https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css -o /app/static/vendor/vis-network.min.css

EXPOSE 8080

ENV PORT=8080 WORKERS=2 LOG_LEVEL=info SERVER_CMD=gunicorn
CMD ["bash","-lc","if [ \"$SERVER_CMD\" = uvicorn ]; then uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --log-level ${LOG_LEVEL:-info}; else gunicorn -w ${WORKERS:-2} -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:${PORT:-8080} --timeout 60 --log-level ${LOG_LEVEL:-info}; fi"]
