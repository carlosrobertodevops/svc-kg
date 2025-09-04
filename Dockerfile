FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    WORKERS=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências primeiro (cache mais eficiente)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia TODO o projeto (com .dockerignore para não levar lixo)
COPY . .

# Garante que as pastas existam (caso não estejam no repo)
RUN mkdir -p /app/static /app/assets /app/docs

EXPOSE 8080

# gunicorn + uvicorn worker = produção
CMD ["bash", "-lc", "exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WORKERS:-2} -b 0.0.0.0:${PORT:-8080} app:app --access-logfile - --error-logfile -"]