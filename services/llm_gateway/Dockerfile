FROM python:3.11-slim

WORKDIR /app

COPY services/llm_gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ /app/services/
COPY libs/ /app/libs/

ENV PORT=8002

EXPOSE 8002

CMD ["uvicorn", "services.llm_gateway.app:app", "--host", "0.0.0.0", "--port", "8002"]
