FROM python:3.11.18-slim

WORKDIR /app

COPY services/llm_stub/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ /app/services/

ENV PORT=8003
ENV STUB_DELAY_MS=1500

EXPOSE 8003

CMD ["uvicorn", "services.llm_stub.app:app", "--host", "0.0.0.0", "--port", "8003"]
