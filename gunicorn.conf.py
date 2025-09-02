# gunicorn.conf.py
import multiprocessing, os

bind = "0.0.0.0:8080"
workers = int(os.getenv("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2)))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
keepalive = 5
accesslog = "-"
errorlog = "-"
