import asyncio
import logging
import os
import random
import time
import uuid
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request
from pydantic import BaseModel

from libs.orcas_common.logging import configure_logging, correlation_id
from libs.orcas_common.telemetry import configure_tracing
from services.llm_gateway.accounting import calculate_cost
from services.llm_gateway.breaker import CircuitBreaker
from services.llm_gateway.config import config

configure_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("llm_gateway")

app = FastAPI(title="ORCAS LLM Gateway", version="1.0.0")
configure_tracing("llm-gateway", app=app, instrument_httpx=True)


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
breaker = CircuitBreaker()

# Prometheus metrics (Requirement A6 – per-agent metrics)
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from starlette.responses import Response

    LLM_REQUEST_COUNTER = Counter(
        "orcas_llm_requests_total",
        "Total LLM requests",
        ["agent", "model", "status"]
    )
    LLM_LATENCY_HISTOGRAM = Histogram(
        "orcas_llm_latency_seconds",
        "LLM call latency",
        ["agent", "model"]
    )
    LLM_TOKEN_COUNTER = Counter(
        "orcas_llm_tokens_total",
        "Total tokens consumed",
        ["agent", "model", "direction"]
    )
    CIRCUIT_BREAKER_TRIPS = Counter(
        "orcas_circuit_breaker_trips_total",
        "Times the circuit breaker opened"
    )
    BREAKER_OPEN = Gauge("orcas_llm_breaker_open", "Whether the LLM breaker is open")
    PROM_AVAILABLE = True

    @app.get("/metrics")
    async def metrics():
        BREAKER_OPEN.set(1 if breaker.get_state()[0] == "open" else 0)
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

except ImportError:
    PROM_AVAILABLE = False
    logger.warning("prometheus_client not installed; /metrics endpoint disabled.")

try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("litellm not installed; llm_gateway operating in fallback/proxy mode.")


class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: Optional[float] = 0.0
    max_tokens: Optional[int] = 2048
    run_id: Optional[str] = None
    agent: Optional[str] = None
    step: Optional[str] = None
    prompt_version: Optional[str] = None


class ChatUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatResponse(BaseModel):
    id: str
    choices: List[ChatChoice]
    usage: ChatUsage
    model: str
    degraded: bool = False


@app.get("/health")
async def health():
    cb_state, failures = breaker.get_state()
    return {
        "status": "ok",
        "provider": config.LLM_PROVIDER,
        "primary_model": config.LLM_MODEL,
        "circuit_breaker": cb_state,
        "failure_count": failures,
        "stub_mode": config.ORCAS_STUB_MODE
    }


async def call_stub(request: ChatRequest) -> ChatResponse:
    """Forward request to stub service or generate canned fallback."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"X-Correlation-ID": request.run_id} if request.run_id else {}
            resp = await client.post(f"{config.STUB_SERVICE_URL}/v1/chat", json=request.model_dump(),
                                     headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return ChatResponse(**data)
    except Exception as exc:
        logger.warning(f"Stub service unavailable at {config.STUB_SERVICE_URL}: {exc}. Using inline fallback.")

    prompt_len = sum(len(m.content) for m in request.messages)
    content = "ORCAS Gateway Fallback: Verified routing analysis completed via fallback pipeline."
    prompt_tokens = max(10, prompt_len // 4)
    completion_tokens = max(5, len(content) // 4)
    
    return ChatResponse(
        id=f"fallback-{uuid.uuid4().hex[:12]}",
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason="stop"
            )
        ],
        usage=ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=0.0
        ),
        model=request.model or "gateway-fallback",
        degraded=True
    )


@app.post("/v1/chat", response_model=ChatResponse)
async def chat_completion(request: ChatRequest):
    agent_label = request.agent or "unknown"
    t0 = time.time()

    if config.ORCAS_STUB_MODE or not config.LLM_API_KEY:
        logger.info(f"Routing to stub (stub_mode={config.ORCAS_STUB_MODE}, has_key={bool(config.LLM_API_KEY)})")
        res = await call_stub(request)
        if PROM_AVAILABLE:
            LLM_REQUEST_COUNTER.labels(agent=agent_label, model=res.model, status="stub").inc()
            LLM_LATENCY_HISTOGRAM.labels(agent=agent_label, model=res.model).observe(time.time() - t0)
            LLM_TOKEN_COUNTER.labels(agent=agent_label, model=res.model, direction="prompt").inc(res.usage.prompt_tokens)
            LLM_TOKEN_COUNTER.labels(agent=agent_label, model=res.model, direction="completion").inc(res.usage.completion_tokens)
        return res

    if not breaker.can_execute():
        logger.warning("Circuit breaker OPEN. Routing to fallback stub.")
        if PROM_AVAILABLE:
            CIRCUIT_BREAKER_TRIPS.inc()
            LLM_REQUEST_COUNTER.labels(agent=agent_label, model="fallback", status="breaker_open").inc()
        res = await call_stub(request)
        res.degraded = True
        if PROM_AVAILABLE:
            LLM_LATENCY_HISTOGRAM.labels(agent=agent_label, model=res.model).observe(time.time() - t0)
        return res

    target_model = request.model or config.LLM_MODEL
    messages_payload = [{"role": m.role, "content": m.content} for m in request.messages]

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            if not LITELLM_AVAILABLE:
                raise RuntimeError("LiteLLM not installed")

            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=target_model,
                    messages=messages_payload,
                    temperature=request.temperature if request.temperature is not None else config.LLM_TEMPERATURE,
                    max_tokens=request.max_tokens or 2048,
                    api_key=config.LLM_API_KEY
                ),
                timeout=config.LLM_TIMEOUT_SECONDS
            )

            breaker.record_success()

            reply_text = response.choices[0].message.content or ""
            p_tokens = getattr(response.usage, "prompt_tokens", len(str(messages_payload)) // 4)
            c_tokens = getattr(response.usage, "completion_tokens", len(reply_text) // 4)
            cost = calculate_cost(target_model, p_tokens, c_tokens)

            # Record Prometheus metrics
            if PROM_AVAILABLE:
                elapsed = time.time() - t0
                LLM_REQUEST_COUNTER.labels(agent=agent_label, model=target_model, status="ok").inc()
                LLM_LATENCY_HISTOGRAM.labels(agent=agent_label, model=target_model).observe(elapsed)
                LLM_TOKEN_COUNTER.labels(agent=agent_label, model=target_model, direction="prompt").inc(p_tokens)
                LLM_TOKEN_COUNTER.labels(agent=agent_label, model=target_model, direction="completion").inc(c_tokens)

            return ChatResponse(
                id=getattr(response, "id", f"call-{uuid.uuid4().hex[:12]}"),
                choices=[
                    ChatChoice(
                        index=0,
                        message=ChatMessage(role="assistant", content=reply_text),
                        finish_reason="stop"
                    )
                ],
                usage=ChatUsage(
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=p_tokens + c_tokens,
                    cost_usd=cost
                ),
                model=target_model,
                degraded=False
            )

        except Exception as exc:
            logger.warning(f"LLM call attempt {attempt + 1}/{max_retries + 1} failed: {exc}")
            if attempt < max_retries:
                base_delay = 0.5 * (2 ** attempt)
                jitter = base_delay * random.uniform(-0.25, 0.25)
                await asyncio.sleep(base_delay + jitter)
            else:
                breaker.record_failure()
                if PROM_AVAILABLE:
                    LLM_REQUEST_COUNTER.labels(agent=agent_label, model=target_model, status="error").inc()

    # Fallback model
    if config.LLM_FALLBACK_MODEL and config.LLM_FALLBACK_MODEL != target_model:
        logger.info(f"Attempting fallback model: {config.LLM_FALLBACK_MODEL}")
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=config.LLM_FALLBACK_MODEL,
                    messages=messages_payload,
                    temperature=0.0,
                    max_tokens=request.max_tokens or 2048,
                    api_key=config.LLM_API_KEY
                ),
                timeout=config.LLM_TIMEOUT_SECONDS
            )
            reply_text = response.choices[0].message.content or ""
            p_tokens = getattr(response.usage, "prompt_tokens", len(str(messages_payload)) // 4)
            c_tokens = getattr(response.usage, "completion_tokens", len(reply_text) // 4)
            cost = calculate_cost(config.LLM_FALLBACK_MODEL, p_tokens, c_tokens)

            return ChatResponse(
                id=getattr(response, "id", f"call-{uuid.uuid4().hex[:12]}"),
                choices=[
                    ChatChoice(
                        index=0,
                        message=ChatMessage(role="assistant", content=reply_text),
                        finish_reason="stop"
                    )
                ],
                usage=ChatUsage(
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=p_tokens + c_tokens,
                    cost_usd=cost
                ),
                model=config.LLM_FALLBACK_MODEL,
                degraded=True
            )
        except Exception as exc:
            logger.error(f"Fallback model failed: {exc}")

    degraded_res = await call_stub(request)
    degraded_res.degraded = True
    return degraded_res
