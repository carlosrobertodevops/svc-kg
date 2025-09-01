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
COPY src/ /app/

EXPOSE 8080
ENV WEB_CONCURRENCY=2
CMD ["bash","-lc","gunicorn -w ${WEB_CONCURRENCY} -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:8080 --timeout 60 --log-level info"]
