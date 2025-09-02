FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Porta padrão
EXPOSE 8080

# Gunicorn + UvicornWorker
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
