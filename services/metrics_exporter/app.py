"""Prometheus view of persisted run events and queue state."""

from __future__ import annotations

import os

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    generate_latest,
)

app = FastAPI(title="ORCAS Worker Metrics")


def collect_snapshot() -> dict:
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        statuses = dict(connection.execute(
            "SELECT status, COUNT(*) FROM runs GROUP BY status"
        ).fetchall())
        latency = connection.execute(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM finished_at-submitted_at)), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM finished_at-submitted_at)) "
            "FROM runs WHERE finished_at IS NOT NULL AND submitted_at > NOW() - INTERVAL '1 hour'"
        ).fetchone()
        events = connection.execute(
            "SELECT COUNT(*) FILTER (WHERE kind = 'retry'), "
            "COUNT(*) FILTER (WHERE kind = 'tool_call'), "
            "COUNT(*) FILTER (WHERE kind = 'llm_call'), "
            "COALESCE(SUM((tokens->>'prompt')::int), 0), "
            "COALESCE(SUM((tokens->>'completion')::int), 0), "
            "COALESCE(SUM(cost_usd), 0) FROM run_events"
        ).fetchone()
        feedback = dict(connection.execute(
            "SELECT rating, COUNT(*) FROM feedback GROUP BY rating"
        ).fetchall())
        scores = dict(connection.execute(
            "SELECT metric, AVG(score) FROM eval_scores GROUP BY metric"
        ).fetchall())
        step_latency = connection.execute(
            "SELECT step, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) "
            "FROM run_events WHERE latency_ms > 0 GROUP BY step"
        ).fetchall()
        llm_by_step = connection.execute(
            "SELECT step, COALESCE(SUM((tokens->>'prompt')::int), 0), "
            "COALESCE(SUM((tokens->>'completion')::int), 0), "
            "COALESCE(SUM(cost_usd), 0) "
            "FROM run_events WHERE kind = 'llm_call' GROUP BY step"
        ).fetchall()
    queue_depth = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2).llen("orcas_tasks")
    return {"statuses": statuses, "latency": latency, "events": events,
            "feedback": feedback, "scores": scores, "step_latency": step_latency,
            "llm_by_step": llm_by_step,
            "queue_depth": queue_depth}


def render_metrics(snapshot: dict) -> bytes:
    registry = CollectorRegistry()

    def gauge(name, description, labels=()):
        return Gauge(name, description, labels, registry=registry)

    statuses = snapshot["statuses"]
    status_gauge = gauge("orcas_runs_status", "Persisted runs by status", ["status"])
    for status in ("queued", "running", "completed", "escalated", "failed"):
        status_gauge.labels(status).set(statuses.get(status, 0))
    terminal = sum(statuses.get(status, 0) for status in ("completed", "escalated", "failed"))
    success = statuses.get("completed", 0) + statuses.get("escalated", 0)
    gauge("orcas_runs_success_ratio", "Fraction of terminal runs that succeeded").set(
        success / terminal if terminal else 0
    )
    latency_gauge = gauge("orcas_run_latency_seconds", "End-to-end latency over the last hour", ["quantile"])
    for quantile, value in zip(("0.5", "0.95"), snapshot["latency"]):
        latency_gauge.labels(quantile).set(float(value or 0))

    retries, tool_calls, llm_calls, prompt_tokens, completion_tokens, cost_usd = snapshot["events"]
    gauge("orcas_run_retries", "Persisted verifier retry events").set(retries or 0)
    gauge("orcas_run_tool_calls", "Persisted retrieval tool calls").set(tool_calls or 0)
    tokens = gauge("orcas_run_tokens", "Persisted LLM tokens", ["direction"])
    tokens.labels("prompt").set(prompt_tokens or 0)
    tokens.labels("completion").set(completion_tokens or 0)
    step_tokens = gauge("orcas_run_tokens_by_step", "Persisted LLM tokens by step",
                        ["step", "direction"])
    step_cost = gauge("orcas_run_cost_by_step_usd", "Persisted LLM cost by step in USD",
                      ["step"])
    for name, prompt, completion, cost in snapshot.get("llm_by_step", []):
        step_tokens.labels(name, "prompt").set(prompt)
        step_tokens.labels(name, "completion").set(completion)
        step_cost.labels(name).set(float(cost))
    gauge("orcas_run_cost_usd", "Persisted LLM cost in USD").set(float(cost_usd or 0))
    gauge("orcas_cost_per_100_runs_usd", "Persisted LLM cost per 100 terminal runs").set(
        float(cost_usd or 0) * 100 / terminal if terminal else 0
    )
    gauge("orcas_run_steps_per_run", "Average persisted work events per run").set(
        ((retries or 0) + (tool_calls or 0) + (llm_calls or 0)) / terminal if terminal else 0
    )
    gauge("orcas_run_tool_calls_per_run", "Average persisted tool calls per run").set(
        (tool_calls or 0) / terminal if terminal else 0
    )
    gauge("orcas_celery_queue_depth", "Waiting Celery tasks in Redis").set(snapshot["queue_depth"])

    feedback = gauge("orcas_feedback", "Persisted feedback ratings", ["rating"])
    for rating in ("up", "down"):
        feedback.labels(rating).set(snapshot["feedback"].get(rating, 0))
    score = gauge("orcas_eval_score", "Average persisted evaluation score", ["metric"])
    for metric, value in snapshot["scores"].items():
        score.labels(metric).set(float(value))
    step = gauge("orcas_run_step_latency_seconds", "Persisted step latency", ["step", "quantile"])
    for name, p50, p95 in snapshot["step_latency"]:
        step.labels(name, "0.5").set(float(p50 or 0) / 1000)
        step.labels(name, "0.95").set(float(p95 or 0) / 1000)
    return generate_latest(registry)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        collect_snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="metrics dependencies unavailable") from exc
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    try:
        return Response(render_metrics(collect_snapshot()), media_type=CONTENT_TYPE_LATEST)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="metrics unavailable") from exc
