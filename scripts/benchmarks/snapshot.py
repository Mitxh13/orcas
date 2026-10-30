"""Print a compact database and queue snapshot from inside the gateway image."""

import json
import os
from datetime import datetime, timezone

import psycopg
import redis

with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    statuses = dict(connection.execute(
        "SELECT status, COUNT(*) FROM runs GROUP BY status"
    ).fetchall())
    llm_calls, tokens, cost_usd, cache_hits = connection.execute(
        "SELECT COUNT(*) FILTER (WHERE kind = 'llm_call'), "
        "COALESCE(SUM((tokens->>'prompt')::int + (tokens->>'completion')::int), 0), "
        "COALESCE(SUM(cost_usd), 0), "
        "COUNT(*) FILTER (WHERE kind = 'cache_hit') FROM run_events"
    ).fetchone()

queue_depth = redis.Redis.from_url(os.environ["CELERY_BROKER_URL"], socket_timeout=3).llen(
    "orcas_tasks"
)
print(json.dumps({
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "statuses": statuses,
    "llm_calls": llm_calls,
    "tokens": tokens,
    "cost_usd": float(cost_usd),
    "cache_hits": cache_hits,
    "queue_depth": queue_depth,
}, sort_keys=True))
