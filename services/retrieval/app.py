"""FastAPI app exposing the retrieval contract.

Run (from repo root, venv active):
    uvicorn services.retrieval.app:app --port 8001
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from libs.orcas_common.logging import configure_logging, correlation_id
from libs.orcas_common.telemetry import configure_tracing

from . import metrics
from .models import (
    Chunk,
    Clause,
    Health,
    IndexInfo,
    SearchRequest,
    SearchResponse,
)
from .service import Retriever

app = FastAPI(title="ORCAS Retrieval Service", version="1.0.0")
configure_tracing("retrieval", app=app)
configure_logging(os.getenv("LOG_LEVEL", "INFO"))


@app.middleware("http")
async def correlate_request(request: Request, call_next):
    candidate = request.headers.get("X-Correlation-ID", "")
    value = candidate if 0 < len(candidate) <= 128 and candidate.isprintable() else str(uuid.uuid4())
    token = correlation_id.set(value)
    try:
        response = await call_next(request)
        response.headers.setdefault("X-Correlation-ID", value)
        return response
    finally:
        correlation_id.reset(token)
_retriever = Retriever()

# Every request is bounded by a timeout so a slow query returns 504 rather than hanging.
RETRIEVAL_TIMEOUT_S = float(os.getenv("RETRIEVAL_TIMEOUT_S", "10"))
_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("RETRIEVAL_WORKERS", "4")))


def with_timeout(fn, *args, timeout: float = None):
    """Run fn with a wall-clock timeout; raise HTTP 504 if it overruns."""
    fut = _EXECUTOR.submit(fn, *args)
    try:
        return fut.result(timeout=timeout if timeout is not None else RETRIEVAL_TIMEOUT_S)
    except FuturesTimeout:
        raise HTTPException(status_code=504, detail="retrieval timed out")


@app.post("/v1/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    try:
        resp = with_timeout(_retriever.search, req)
    except HTTPException:
        metrics.REQUESTS.labels(endpoint="search", status="timeout").inc()
        raise
    metrics.SEARCH_LATENCY.observe(resp.latency_ms / 1000.0)
    metrics.RESULTS_PER_QUERY.observe(resp.count)
    (metrics.CACHE_HITS if resp.cache_hit else metrics.CACHE_MISSES).inc()
    metrics.REQUESTS.labels(endpoint="search", status="ok").inc()
    return resp


@app.get("/v1/clauses/{doc_id}/{clause_id}", response_model=Clause)
def get_clause(doc_id: str, clause_id: str) -> Clause:
    clause = _retriever.get_clause(doc_id, clause_id)
    if clause is None:
        raise HTTPException(status_code=404, detail="no such clause in this document")
    return clause


@app.get("/v1/chunks/{chunk_id}", response_model=Chunk)
def get_chunk(chunk_id: str, quote_span: str | None = Query(default=None)) -> Chunk:
    chunk = _retriever.get_chunk(chunk_id, quote_span=quote_span)
    if chunk is None:
        raise HTTPException(status_code=404, detail="chunk does not resolve")
    return chunk


@app.get("/v1/index/info", response_model=IndexInfo)
def index_info() -> IndexInfo:
    info = _retriever.info()
    if info is None:
        raise HTTPException(status_code=503, detail="index not built yet")
    return info


@app.get("/metrics")
def prometheus_metrics() -> Response:
    metrics.refresh_index_gauges()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health", response_model=Health)
def health() -> JSONResponse:
    """Liveness: 200 as long as the process is up (never flaps while ingest is still building)."""
    return JSONResponse(status_code=200, content=Health(
        status="ok", model_loaded=True, index_version=_retriever.index_version()).model_dump())


@app.get("/ready", response_model=Health)
def ready() -> JSONResponse:
    """Readiness: 200 only once the index is openable and non-empty (what compose waits on)."""
    try:
        count = _retriever.col.count()
        index_openable = True
    except Exception:
        count = 0
        index_openable = False
    ok = index_openable and count > 0
    h = Health(
        status="ok" if ok else "not_ready",
        model_loaded=True,
        index_openable=index_openable,
        index_version=_retriever.index_version(),
    )
    return JSONResponse(status_code=200 if ok else 503, content=h.model_dump())
