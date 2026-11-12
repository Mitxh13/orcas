# ORCAS ingest one-shot (idempotent corpus pipeline).
# Build from the repo root (needs requirements.txt, services/ and corpus/):
#   docker build -f services/ingest/Dockerfile -t orcas-ingest .
# Runs once, writes the Chroma index + index_info.json to the /data volume that the
# retrieval service reads, then exits. Re-running is a no-op when the corpus is unchanged.
FROM python:3.11.18-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models \
    ANONYMIZED_TELEMETRY=False \
    ORCAS_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY services/ingest/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# Same baked model as the retrieval image (consistent embeddings, no runtime download).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY services/ ./services/
COPY corpus/ ./corpus/

# Default: index every indexed_ready document in corpus/MANIFEST.yaml into /data.
CMD ["python", "-m", "services.ingest"]
