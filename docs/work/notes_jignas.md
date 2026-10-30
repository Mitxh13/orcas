# Jignas implementation notes

## 2026-10-26 — Initial demo path

- Added a shared run-store interface with in-memory and Postgres implementations.
  The first migration creates `runs`, `run_events`, `quarantine`, `feedback`,
  and `eval_scores`.
- Added the real FastAPI gateway. It validates the frozen complaint fields and
  cross-field date rules before creating a run or sending a Celery task. Rejected
  bodies receive field-level errors and a persisted quarantine ID.
- Connected the worker to the shared store and retrieval HTTP API when those
  services are configured. Duplicate terminal runs return their existing result.
- Added the initial Compose stack and corrected the service image build paths.
  The LLM stub is the default for the first demo. Its draft citations now come
  from the current retrieval evidence rather than old fixture IDs.
- Added an initial demo script and a guide for showing a valid run and a
  quarantined malformed request.

**Checks performed:** platform and agent tests passed locally; the Postgres
migration was applied twice to a temporary PostgreSQL 17 database, and the
database-backed intake test passed. Docker is not installed on this machine, so
the Compose stack has not yet been started here. A clean-clone run on a Docker
host remains the release gate for the initial demo.

## 2026-10-27 — Reliability and operations work

- Added pre-enqueue prompt-injection and explicit-threat checks for both free-text
  fields. The labelled regression set has 21 attack examples and 20 benign
  complaints; no benign examples were flagged in that set.
- Added protected quarantine review, persisted feedback and verification
  scores, ordered run trace responses, a Redis token bucket, and a completed-run
  cache versioned by input, retrieval index, and prompt file contents.
- Declared the stub-backed 90-second p95 target in `docs/slo.md` before adding
  the Locust driver. The driver measures submit-to-terminal latency and has
  separate cache comparison modes.
- Added correlated HTTP headers and JSON logging for gateway, retrieval, and
  LLM gateway, plus persisted retrieval/LLM events from the worker. Added
  OTLP→collector→Jaeger configuration and file-provisioned Prometheus/Grafana.
- Added a read-only worker metrics exporter for queue depth, run latency,
  status, cost, tokens, feedback, and verification scores.

**Checks performed:** platform/agent/web tests, targeted metrics and adapter
tests, Ruff, and YAML/JSON parsing. Docker is still unavailable locally, so
Compose startup, a cross-service Jaeger trace, load/scaling numbers, and
controlled failure checks remain unverified. No performance or cost result has
been inferred from the static checks.

## 2026-10-27 — Clean runner smoke test

GitHub Actions built the Compose stack from a clean checkout and passed all
service readiness checks. Its first end-to-end run exposed an unregistered
Celery task: worker tracing imported FastAPI, absent from the worker image, and
a broad `ImportError` handler hid the failure. Commit `5672df4` corrected the
import boundary and added a regression test. The repeat
[Compose smoke run](https://github.com/Mitxh13/orcas/actions/runs/40450433329)
passed: the bank fixed-deposit complaint completed as an RBI referral and a
malformed date was quarantined. Load, scaling, trace UI inspection, and failure
drills have not been measured on a Docker host.

## 2026-10-27 — Live observability verification

The [Compose smoke run](https://github.com/Mitxh13/orcas/actions/runs/40543154248)
passed a live Jaeger query after a completed complaint. Trace
`b995f31e8bacdf73834f712c08dd1c3e` includes gateway, worker, retrieval,
and LLM gateway spans. The worker span has a parent reference to the gateway's
queue-publish span. Prometheus reported healthy scrapes for gateway,
retrieval, LLM gateway, worker metrics, cAdvisor, and itself; run latency,
gateway requests, and queue depth had live series. Grafana served the
file-provisioned ORCAS dashboard with 21 panels.

## 2026-10-27 — Controlled recovery drills

The first [failure-drill run](https://github.com/JignasP/orcas/actions/runs/40540759058)
confirmed worker and Redis recovery, then the test's retrieval readiness probe
stopped on a transient connection reset after restart. Commit `22c8f08`
made that probe retry connection resets. The repeat
[failure-drill run](https://github.com/JignasP/orcas/actions/runs/40542611618)
passed all three cases. With the worker stopped, run
`b4704b6c-09c4-455f-93a2-a9cdfa6f63b3` stayed queued and completed after
restart. During the Redis outage intake returned `503`; a new run
`79a92dee-73f8-4b9b-82dc-3920968e2281` completed after restart. During
the retrieval outage run `59c57a67-9a37-4747-9906-a5b2dc5f8fc7` failed;
after restart, a new run `0e480f6d-9457-4af1-8a27-3692e587c5fb`
completed. No automatic retry of the failed retrieval run was verified.

## 2026-10-27 — Input guardrail demo on Compose

The [Compose smoke run](https://github.com/Mitxh13/orcas/actions/runs/40546391500)
submitted a valid-format complaint containing an instruction to ignore prior
instructions. The gateway returned `422` with `guardrail:injection` for `text`
and quarantine ID `c29f5bc1-5e2d-4551-a083-2744cf6cd0ca`. Its database
assertion found one completed run and two quarantined requests after the
valid, malformed-date, and injection submissions; the injection request
created no additional run.

## 2026-10-27 — Stub-backed load and cache measurements

The [benchmark run](https://github.com/JignasP/orcas/actions/runs/40540516292)
completed all Locust phases with exit code zero. At ten concurrent clients,
363 end-to-end observations had p50 23 seconds, p95 40 seconds, throughput
0.387/s, and zero failures, meeting the committed 90-second/99% SLO. Three
workers at ten clients produced p50 6.1 seconds, p95 20 seconds, and
1.159/s. One worker at 20 clients had p95 70 seconds; at 40 clients p95
rose to 123 seconds and breached the target. Throughput plateaued near
0.37/s from ten to 40 clients, pointing to worker capacity as the likely
limit. End-of-phase queue snapshots were zero after Locust drained, so a
separate live queue/CPU capture was started to confirm the peak.

After cache warmup, repeated requests had p95 0.32 seconds, compared with
42 seconds in the bypass phase. The repeat snapshot contained 1,563 cache
hits and zero new LLM calls or tokens; bypass recorded 272 LLM calls and
293,199 tokens. The snapshot included four completions across the phase
boundary, so its event count is not an exact per-request hit ratio for the
1,559 Locust observations. The stub's monetary cost was zero in both phases;
no production cost estimate follows from it.

## 2026-10-27 — Low-load ramp and queue diagnosis

The [supplemental load-ramp run](https://github.com/JignasP/orcas/actions/runs/40551412411)
measured one client (30 observations, p95 17 seconds), five clients (136,
p95 23 seconds), and 40 clients (162, p95 109 seconds), all with zero Locust
failures. Prometheus queue samples peaked at 0, 3, and 38 respectively;
mean queue depth at 40 clients was 31.1. This supports worker process slots
as the limiting component at high concurrency. The cAdvisor target was up,
but the resource query filtered by `name=~".*orcas.*"` returned no CPU or
memory series. The follow-up Compose smoke run found that cAdvisor emits
Docker cgroup IDs in `id` with no `name` label on this runner. The dashboard
and range capture now query Docker cgroup IDs and map them to Compose services
through the Docker CLI. The fork's later cAdvisor range capture still did not
match Compose containers, so Docker stats was added for per-service load
measurements.

## 2026-10-29 — Docker resource panel and usage breakdown

The [repeat Compose check](https://github.com/Mitxh13/orcas/actions/runs/40892881628)
passed the actual Grafana CPU and memory panel queries against live Prometheus
data. cAdvisor exposed 15 Docker cgroup series for each metric. The dashboard
now includes a five-minute service availability view and cumulative LLM tokens
and cost by step, sourced from persisted `llm_call` events. The clean-runner
check also passed with 24 provisioned panels and live quality and per-step
LLM usage metrics.

## 2026-10-29 — Docker stats load-ramp result

The [Docker stats ramp](https://github.com/JignasP/orcas/actions/runs/40900547182)
completed all three Locust phases and sampled CPU and memory every ten seconds
while load was running. At one, five, and 40 clients, queue depth peaked at
0, 3, and 38. The 40-client phase had 153 successful observations, p95
123 seconds, and throughput 0.393/s. The worker's sampled maximum was
81.03% CPU and 289.1 MiB memory, above the application CPU peaks of gateway
(35.23%) and PostgreSQL (37.42%). Retrieval used 3.59% CPU and 496.7 MiB.
These samples support worker execution capacity as the limiting component
alongside queue growth and the three-worker scaling result; they do not show
the worker pinned at 100% CPU. The fork runner's cAdvisor range query exposed
host cgroups but no Compose container IDs, while the original-repository
Compose smoke verified live Grafana resource panel queries. The benchmark
therefore reports Docker stats for service-level resource figures.
