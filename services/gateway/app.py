"""FastAPI ingestion boundary for synthetic regulatory complaints."""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from libs.orcas_common.logging import configure_logging, correlation_id
from libs.orcas_common.run_cache import RedisRunCache
from libs.orcas_common.runstore import PostgresRunStore
from libs.orcas_common.telemetry import configure_tracing
from services.gateway.input_guardrails import check_free_text
from services.gateway.metrics import (
    CACHE_LOOKUPS,
    LATENCY,
    RATE_LIMIT_REJECTIONS,
    REQUESTS,
)
from services.gateway.models import (
    Complaint,
    FeedbackRequest,
    RunStatus,
    SubmitResponse,
    cross_field_errors,
)
from services.gateway.queue import CeleryDispatcher
from services.gateway.rate_limit import RedisRateLimiter

logger = logging.getLogger(__name__)


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [{
        "field": ".".join(str(part) for part in item["loc"]) or "body",
        "message": item["msg"],
        "type": item["type"],
    } for item in error.errors()]


def create_app(store=None, dispatcher=None, limiter=None, cache=None) -> FastAPI:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    store = store or PostgresRunStore()
    dispatcher = dispatcher or CeleryDispatcher()
    if limiter is None and os.getenv("REDIS_URL"):
        limiter = RedisRateLimiter(os.environ["REDIS_URL"], int(os.getenv("RATE_LIMIT_PER_MIN", "60")))
    if cache is None and os.getenv("REDIS_URL") and os.getenv("RETRIEVAL_SERVICE_URL"):
        cache = RedisRunCache(
            os.environ["REDIS_URL"], int(os.getenv("RUN_CACHE_TTL_S", "3600")),
            os.environ["RETRIEVAL_SERVICE_URL"], Path(__file__).parents[2] / "prompts",
        )
    app = FastAPI(title="ORCAS Gateway", version="1.0.0")
    configure_tracing("gateway", app=app, instrument_httpx=True)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
        ).split(",") if origin.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Correlation-ID", "X-Run-Cache-Bypass", "Authorization"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        started = time.monotonic()
        candidate = request.headers.get("X-Correlation-ID", "")
        request_id_value = candidate if 0 < len(candidate) <= 128 and candidate.isprintable() else str(uuid.uuid4())
        token = correlation_id.set(request_id_value)
        try:
            response = await call_next(request)
            response.headers.setdefault("X-Correlation-ID", request_id_value)
            if request.url.path != "/metrics":
                route = request.scope.get("route")
                route_name = getattr(route, "path", "unmatched")
                REQUESTS.labels(request.method, route_name, str(response.status_code)).inc()
                LATENCY.labels(request.method, route_name).observe(time.monotonic() - started)
            return response
        finally:
            correlation_id.reset(token)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        try:
            store.ping()
            dispatcher.ping()
        except Exception:
            logger.exception("gateway dependencies are unavailable")
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post(
        "/v1/complaints", status_code=202, response_model=SubmitResponse,
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": Complaint.model_json_schema()}
        }}},
    )
    async def submit(request: Request):
        raw = await request.body()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            errors = [{"field": "body", "message": "invalid JSON", "type": "json_invalid"}]
            quarantine_id = store.quarantine(raw.decode("utf-8", errors="replace"), "validation", errors)
            return JSONResponse(status_code=422, content={"detail": errors, "quarantine_id": quarantine_id})

        try:
            complaint = Complaint.model_validate_json(raw, strict=True)
            errors = cross_field_errors(complaint)
        except ValidationError as error:
            errors = _validation_errors(error)
            complaint = None
        if errors:
            quarantine_id = store.quarantine(data, "validation", errors)
            return JSONResponse(status_code=422, content={"detail": errors, "quarantine_id": quarantine_id})

        errors = check_free_text({"text": complaint.text, "entity_name_hint": complaint.entity_name_hint})
        if errors:
            reason = errors[0]["type"]
            quarantine_id = store.quarantine(data, reason, errors)
            return JSONResponse(status_code=422, content={"detail": errors, "quarantine_id": quarantine_id})

        if limiter is not None:
            try:
                retry_after = limiter.retry_after(complaint.client_id)
            except Exception:
                logger.exception("rate limiter unavailable")
                raise HTTPException(status_code=503, detail="rate limiter unavailable")
            if retry_after:
                RATE_LIMIT_REJECTIONS.inc()
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"},
                                    headers={"Retry-After": str(retry_after)})

        payload = complaint.model_dump(mode="json", exclude_none=True)
        cache_key = None
        if cache is not None and request.headers.get("X-Run-Cache-Bypass", "").lower() != "true":
            try:
                cache_key = cache.key_for(payload)
                cached = cache.get(cache_key) if cache_key else None
            except Exception:
                logger.exception("run cache unavailable; continuing without cache")
                cached = None
                cache_key = None
            CACHE_LOOKUPS.labels("hit" if cached else "miss").inc()
        else:
            cached = None
            if cache is not None:
                CACHE_LOOKUPS.labels("bypass").inc()

        run_id = str(uuid.uuid4())
        correlation_id.set(run_id)
        if cache_key:
            payload["_cache_key"] = cache_key
        store.create_run(run_id, payload)
        if cached:
            cached = dict(cached)
            cached["run_id"] = run_id
            store.complete(run_id, cached)
            store.append_event(run_id, "gateway", "cache", "cache_hit",
                               payload={"cache_key": cache_key})
            return JSONResponse(
                status_code=202, content={"run_id": run_id, "status_url": f"/v1/runs/{run_id}"},
                headers={"X-Correlation-ID": run_id, "X-Run-Cache": "hit"},
            )
        try:
            dispatcher.send(run_id)
        except Exception:
            logger.exception("unable to enqueue run")
            store.fail(run_id, "queue unavailable")
            raise HTTPException(status_code=503, detail="queue unavailable")
        logger.info("complaint accepted")
        return JSONResponse(
            status_code=202,
            content={"run_id": run_id, "status_url": f"/v1/runs/{run_id}"},
            headers={"X-Correlation-ID": run_id},
        )

    @app.get("/v1/runs/{run_id}", response_model=RunStatus)
    def get_run(run_id: uuid.UUID):
        record = store.get_run(str(run_id))
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {key: record.get(key) for key in (
            "run_id", "status", "submitted_at", "started_at", "finished_at", "result"
        )}

    @app.get("/v1/runs/{run_id}/trace")
    def get_trace(run_id: uuid.UUID):
        record = store.get_run(str(run_id))
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        events = store.get_events(str(run_id))
        return {"run_id": str(run_id), "status": record["status"], "events": events,
                "total_cost_usd": sum(float(event.get("cost_usd") or 0) for event in events)}

    @app.post("/v1/runs/{run_id}/feedback", status_code=201)
    def post_feedback(run_id: uuid.UUID, feedback: FeedbackRequest):
        if store.get_run(str(run_id)) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return store.add_feedback(str(run_id), feedback.rating, feedback.reason)

    def require_admin(request: Request) -> None:
        token = os.getenv("ORCAS_ADMIN_TOKEN", "")
        if not token:
            raise HTTPException(status_code=503, detail="admin API is not configured")
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="admin authorization required")

    @app.get("/v1/admin/quarantine")
    def list_quarantine(request: Request, limit: int = Query(default=100, ge=1, le=500)):
        require_admin(request)
        return {"items": store.list_quarantine(limit)}

    @app.get("/v1/admin/feedback/summary")
    def feedback_summary(request: Request):
        require_admin(request)
        return store.feedback_summary()

    return app


app = create_app()
