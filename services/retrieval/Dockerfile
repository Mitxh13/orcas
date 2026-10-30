# ORCAS retrieval service.
# Build from the repo root so requirements.txt and services/ are in context:
#   docker build -f services/retrieval/Dockerfile -t orcas-retrieval .
# The embedding model is baked in at build time, so the container never downloads
# it at runtime (L1 / startup latency). The Chroma index is NOT baked in — it is
# read from the /data volume that the ingest one-shot writes.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models \
    ANONYMIZED_TELEMETRY=False \
    ORCAS_CHROMA_DIR=/data/chroma \
    ORCAS_INDEX_INFO=/data/index_info.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/retrieval/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# Bake the embedding model into the image (no runtime download).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY services/ ./services/
COPY libs/ ./libs/

EXPOSE 8001
# Readiness probe: /ready returns 503 until the index volume is populated by ingest, then 200.
# (/health is liveness and always 200 so the container is not killed while ingest runs.)
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=5 \
  CMD curl -fsS http://localhost:8001/ready || exit 1

CMD ["uvicorn", "services.retrieval.app:app", "--host", "0.0.0.0", "--port", "8001"]
