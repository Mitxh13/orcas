FROM python:3.11-slim

WORKDIR /app
COPY services/metrics_exporter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/__init__.py /app/services/__init__.py
COPY services/metrics_exporter/ /app/services/metrics_exporter/

EXPOSE 8004
CMD ["uvicorn", "services.metrics_exporter.app:app", "--host", "0.0.0.0", "--port", "8004"]
