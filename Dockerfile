# Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    "uvicorn[standard]==0.30.6" \
    "gunicorn==22.0.0" \
    "psycopg[binary]==3.2.1" \
    "psycopg_pool==3.2.1" \
    orjson==3.10.7

WORKDIR /app

# Copia TUDO do contexto (independe de existir src/ ou não)
COPY . /app/

# Por padrão, assume que o arquivo está em src/app.py (src.app:app).
# Se o seu app.py estiver na raiz do repo, ajuste no Coolify: APP_MODULE=app:app
ENV APP_MODULE="src.app:app"

EXPOSE 8080
# Usa bash para interpolar envs (APP_MODULE, WEB_CONCURRENCY)
CMD ["bash","-lc","gunicorn -w ${WEB_CONCURRENCY:-2} -k uvicorn.workers.UvicornWorker ${APP_MODULE} -b 0.0.0.0:8080 --timeout 60 --log-level info"]
