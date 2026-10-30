# ORCAS operations runbook

Use `docker compose ps` first. The gateway's `/health` confirms process liveness;
`/ready` checks Postgres and Redis. Prometheus is at `localhost:9090`, Grafana
at `localhost:3000`, and Jaeger at `localhost:16686`.

## Worker backlog or stalled runs

**Symptoms:** submitted runs remain `queued`, end-to-end p95 exceeds the
[declared target](slo.md), and `orcas_celery_queue_depth` rises.

**Confirm:** check `docker compose ps worker redis postgres`, inspect
`docker compose logs --tail=100 worker`, and compare queue depth with
`orcas_runs_status{status="running"}` in Grafana. Check whether retrieval
`/ready` or the LLM gateway `/health` has failed before adding workers.

**Recover:** restore failed dependencies, then restart unhealthy workers with
`docker compose restart worker`. If dependencies are healthy and CPU is not
saturated, scale with `docker compose up -d --scale worker=3`. Watch queue
depth fall and verify a new synthetic run reaches a terminal status. Do not
purge Redis: queued work would be lost.

## LLM provider outage or open breaker

**Symptoms:** degraded determinations increase, LLM request errors rise, or
`orcas_llm_breaker_open` is `1`.

**Confirm:** inspect `docker compose logs --tail=100 llm-gateway`,
the LLM gateway's `/health` endpoint from inside the Compose network, and the
`orcas_llm_requests_total` status labels. Check whether the provider key,
quota, or network is the cause; never print the key in logs.

**Recover:** restore provider access or temporarily set `ORCAS_STUB_MODE=true`
for a synthetic-only demo, then recreate the LLM gateway. The breaker closes
after a successful provider call. Verify the next run's `degraded` field and
its Jaeger trace before calling the incident resolved.

## Controlled failure checks

The [Compose failure-drill run](https://github.com/JignasP/orcas/actions/runs/40542611618)
used a disposable clean runner and the stub-backed stack:

| Stopped service | Observed during outage | After restart |
|---|---|---|
| Worker | Accepted run stayed `queued` | The same run completed |
| Redis | Intake returned HTTP `503` | A new run completed |
| Retrieval | Accepted run reached `failed` | A new run completed |

Run `python scripts/benchmarks/failure_drills.py` only on a disposable Compose
stack. The script stops one service at a time and writes the outcome to
`loadtest/results/ci/failure-drills.json`. A retrieval outage does not
automatically retry its failed run; submit a new synthetic complaint after
restoring retrieval. Docker is unavailable on this workstation, so these
observations come from GitHub Actions rather than a local run.
