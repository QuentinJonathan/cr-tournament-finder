FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD exec gunicorn \
  --bind 0.0.0.0:${PORT:-8080} \
  --worker-class gthread \
  --workers ${WEB_CONCURRENCY:-1} \
  --threads ${GUNICORN_THREADS:-8} \
  --timeout ${GUNICORN_TIMEOUT:-180} \
  wsgi:app
