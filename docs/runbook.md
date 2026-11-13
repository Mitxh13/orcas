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

For a hosted provider, distinguish HTTP `429` or exhausted quota from a bad
`LLM_API_KEY`; correct `.env` privately and recreate `llm-gateway`. For Ollama,
check `docker compose --profile ollama ps ollama`, its model inventory with
`docker compose exec ollama ollama list`, and the `LLM_API_BASE` setting.
When no model can verify, the run must reach an escalated determination with
`verification_unavailable` or `agent_failure` and `degraded=true`; do not treat
an unverified draft as a completed answer.

## Stale running run

The `worker-scheduler` dispatches a sweep every minute. A run left in
`running` beyond `CELERY_TIME_LIMIT` plus 60 seconds is escalated with
`agent_failure` and `degraded=true`. Check its `run_finished` recovery event.
If the scheduler is down, restart it with `docker compose restart worker-scheduler`
and confirm the run reaches a terminal state. The mid-run worker kill drill
checks redelivery and the sweep path on a disposable stack.

## Controlled failure checks

The [Compose failure-drill run](https://github.com/Mitxh13/orcas/actions/runs/43060243324)
used a disposable CI runner and the stub-backed stack. Its recorded outcomes
are in [`loadtest/results/ci/failure-drills.json`](../loadtest/results/ci/failure-drills.json):

| Stopped service | Observed during outage | After restart |
|---|---|---|
| Worker | Accepted run stayed `queued` | The same run completed |
| Worker killed mid-run | Accepted run was `running` | The same run reached `escalated` |
| Redis | Intake returned HTTP `503` | A new run completed |
| Retrieval | Accepted run reached `escalated` with `agent_failure` and `degraded=true` | A new run completed |

Run `python scripts/benchmarks/failure_drills.py` only on a disposable Compose
stack. The script stops one service at a time and writes the outcome to
`loadtest/results/ci/failure-drills.json`. The outage run is terminal but is
not automatically retried after retrieval returns; submit a new synthetic
complaint after restoring retrieval. The worker kill case demonstrates a
terminal outcome, not a guarantee of completion. Docker's engine was
unavailable on the development workstation, so these observations came from
GitHub Actions.
