# Platform, reliability, and operations report material

## Architecture and boundaries

The FastAPI gateway owns validation, quarantine, rate limiting, cache lookup,
run creation, and Celery dispatch. Redis carries the Celery queue, short-lived
cache entries, and per-client token buckets. PostgreSQL stores run state,
ordered events, quarantine records, feedback, and verification scores. Worker
replicas run the Researcher and Verifier separately from intake. Retrieval owns
the embedded corpus index; the LLM gateway owns provider access and its shared
circuit breaker. [Architecture §2.1](../../ARCHITECTURE.md#21-deployment-boundaries-and-why)
documents why these are separate deployment units.

## Ingestion and safety

The gateway enforces the frozen complaint schema and cross-field date rules
before creating a run. Invalid JSON, malformed fields, and guardrail matches
are quarantined with a `422` response and do not dispatch a Celery task. The
input guardrail uses conservative instruction and threat patterns; the labelled
regression set includes 21 attack examples and 20 benign complaints. It flags
all labelled attacks and none of those 20 benign examples. This is a local
regression result, not a field false-positive estimate. The
[live Compose demo check](https://github.com/Mitxh13/orcas/actions/runs/40546391500)
also rejected an instruction embedded in `text` with `422` and a quarantine
record while leaving the run count unchanged.

## Observability

All run IDs appear as correlation IDs in gateway, worker, retrieval, and LLM
gateway logs and HTTP headers. Run events record searches, passages, prompt
versions, LLM usage and cost, retries, verification, and terminal status.
Prometheus scrapes API metrics and a read-only worker metrics exporter.
Grafana dashboards are provisioned from versioned files. OpenTelemetry sends
traces to Jaeger through a collector. The clean-runner
[Compose smoke check](https://github.com/Mitxh13/orcas/actions/runs/40892881628)
confirmed trace `7d6d68d566d18539640af80cfa3e2ab3` contains gateway,
worker, retrieval, and LLM gateway spans, with the worker continuing the
gateway's queue-publish span. It also confirmed all six Prometheus scrape
targets were healthy, core run and per-step LLM metrics had live series, and
the provisioned Grafana dashboard contained 24 panels, including service
availability and LLM tokens and cost by step.

## SLO and experimental method

The target was committed before load testing: p95 submit-to-terminal latency
at most 90 seconds and at least 99% successful terminal outcomes with ten
concurrent clients, one worker container with two Celery processes, and a
1.5-second stub delay. [The SLO](../../slo.md) defines the complete workload.
Locust records the end-to-end observation separately from POST and poll times.
The same payload mix was run at higher concurrency and with three worker
replicas. Cache impact was measured with a warmup, repeated-input, and bypass
mode at the same rate-limit setting.

## Results and limitations

The [benchmark run](https://github.com/JignasP/orcas/actions/runs/40540516292)
and [resource ramp](https://github.com/JignasP/orcas/actions/runs/40900547182)
used the 1.5-second LLM stub on GitHub Actions Compose runners. Every listed
Locust phase exited successfully. The values below are from each run's
`complaint_end_to_end` row, measured from submission to terminal response;
throughput is terminal observations per second, not gateway request rate.

| Workload | Workers | Clients | Observations | p50 | p95 | Throughput | Failures |
|---|---:|---:|---:|---:|---:|---:|---:|
| Resource ramp | 1 | 1 | 25 | 2.1 s | 17 s | 0.140/s | 0 |
| Resource ramp | 1 | 5 | 105 | 11 s | 25 s | 0.334/s | 0 |
| Baseline | 1 | 10 | 363 | 23 s | 40 s | 0.387/s | 0 |
| Scaled | 3 | 10 | 1,065 | 6.1 s | 20 s | 1.159/s | 0 |
| Higher load | 1 | 20 | 130 | 47 s | 70 s | 0.376/s | 0 |
| Higher load | 1 | 40 | 151 | 98 s | 123 s | 0.370/s | 0 |

The declared ten-client SLO passed: p95 was 40 seconds against 90 seconds,
and all 363 observations succeeded against the 99% success target. The first
tested higher concurrency to breach the latency target was 40 clients; its
p95 was 123 seconds even though all 151 observations reached a successful
terminal state. In the ten-client database snapshot, 101 outcomes completed
and 263 escalated; one terminal outcome crossed the phase boundary, so this
is not an exact outcome split for the 363 Locust observations. Escalation
counts as a successful terminal outcome under the declared SLO, but is not an
automated resolution or a measure of answer quality. Throughput stayed near
0.37/s as concurrency rose from 10 to 40, while latency increased. Three
workers at ten clients halved p95 and
nearly tripled throughput. The baseline and scaled database windows recorded
1,177 and 3,407 LLM calls respectively, about 3.2 calls per observed run in
both phases. At 1.5 seconds per stub call, that is roughly 4.8 seconds of
stub waiting per run. These observations point to the worker's two processing
slots as the limiting component; three replicas provided six slots. The
benchmark snapshots were taken after Locust
drained, so their zero queue depth does not establish the peak queue size.
The separate [resource ramp](https://github.com/JignasP/orcas/actions/runs/40900547182)
sampled queue depth during the experiment: the peak was 0 at one client,
3 at five clients, and 38 at 40 clients; the mean at 40 was 30.9 queued
tasks. Its independent 40-client phase also breached the target with p95
123 seconds across 153 successful observations and throughput 0.393/s. The
queue growth confirms worker backlog. Docker stats was sampled every ten
seconds during each phase. At 40 clients, the worker reached 81.03% CPU and
289.1 MiB memory; gateway reached 35.23% CPU and 94.3 MiB, PostgreSQL
37.42% and 66.7 MiB, and retrieval 3.59% and 496.7 MiB. These are sampled
maxima under Docker's per-core CPU percentage convention, not continuous
peaks. The worker had the highest application CPU sample, but was not pinned
at 100%; queue growth and the three-worker improvement are stronger evidence
for limited worker execution slots than for host CPU exhaustion.

The fork runner's cAdvisor range query returned host cgroups but no matching
Compose container IDs, so the load-run resource breakdown uses Docker stats.
The [original-repository Compose check](https://github.com/Mitxh13/orcas/actions/runs/40900563218)
independently verified that both Grafana resource panel queries returned live
Docker cgroup series. Resource labels vary with runner cgroup layout; the
dashboard can show Docker IDs when cAdvisor does not expose names.

| Cache mode | Observations | p50 | p95 | Throughput | LLM calls during window | Tokens during window |
|---|---:|---:|---:|---:|---:|---:|
| Repeated input after warmup | 1,559 | 0.12 s | 0.32 s | 8.701/s | 0 | 0 |
| Same input with bypass | 76 | 24 s | 42 s | 0.347/s | 272 | 293,199 |

Repeated submissions had a p95 roughly 131 times lower than bypassed
submissions. The database snapshot recorded 1,563 cache-hit events and 1,563
new terminal outcomes during the repeat window, consistent with a 100% hit
ratio among outcomes counted in that window. Locust observed 1,559 requests:
four outcomes crossed the warmup/repeat boundary, so the snapshot cannot
assign an exact hit ratio to those 1,559 observations. The stub recorded
**$0 per 100 runs** in every phase; this is a test-environment figure, not a
production cost estimate. It cannot identify a monetary top driver, although
the bypass window used 293,199 tokens and the repeat window recorded none.

The [controlled failure run](https://github.com/JignasP/orcas/actions/runs/40542611618)
observed a queued run completing after worker restart, HTTP `503` during a
Redis outage followed by a completed new run, and a failed run during a
retrieval outage followed by a completed new run after restart. Docker is
unavailable on this workstation; these results are from clean hosted runners.
