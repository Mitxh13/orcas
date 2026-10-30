FROM python:3.11-slim

WORKDIR /app

COPY services/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY libs/ /app/libs/
COPY services/ /app/services/
COPY prompts/ /app/prompts/

# Worker has no fixed host ports to allow --scale worker=3
CMD ["celery", "-A", "services.worker.tasks:celery_app", "worker", "--loglevel=info", "-Q", "orcas_tasks", "--concurrency=2"]
