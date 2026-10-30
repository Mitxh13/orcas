FROM python:3.11-slim

WORKDIR /app
COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY libs/ /app/libs/
COPY prompts/ /app/prompts/
COPY services/__init__.py /app/services/__init__.py
COPY services/gateway/ /app/services/gateway/

EXPOSE 8000
CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
