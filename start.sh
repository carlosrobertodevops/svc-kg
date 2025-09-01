#!/usr/bin/env bash
set -e

# APP_MODULE pode vir do ambiente; se não vier, detecta automaticamente
MOD="${APP_MODULE:-auto}"

# sempre garanta caminhos no Python
export PYTHONPATH="${PYTHONPATH:-/app:/app/src}"

if [ "$MOD" = "auto" ]; then
  if [ -f /app/src/app.py ]; then MOD="src.app:app"
  elif [ -f /app/app.py ]; then MOD="app:app"
  else
    echo "❌ app.py não encontrado em /app nem /app/src"
    ls -R /app
    sleep 10
    exit 1
  fi
fi

echo "==> Using APP_MODULE=$MOD"
echo "==> PYTHONPATH=$PYTHONPATH"
echo "==> Listening on :8080"
exec gunicorn -w ${WEB_CONCURRENCY:-2} -k uvicorn.workers.UvicornWorker "$MOD" \
  -b 0.0.0.0:8080 --timeout 60 --log-level ${LOG_LEVEL:-info} --access-logfile -
