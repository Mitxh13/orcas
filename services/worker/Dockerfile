FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Worker has no fixed host ports to allow --scale worker=3
CMD ["celery", "-A", "tasks.celery_app", "worker", "--loglevel=info", "-Q", "orcas_tasks"]
