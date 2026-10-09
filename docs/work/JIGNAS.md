# JIGNAS: Platform, Reliability & Operations

**Role in one line:** you build the production shell around the agents: ingestion boundary, async execution, Docker Compose, observability, load testing, scaling analysis and the runbook.

**The project awards zero marks if `docker compose up` doesn't start and serve a valid request.** You own that sentence. Treat it as your top priority from Day 1.

---

## 1. What you own

### Requirements
| ID | Requirement | Your piece |
|---|---|---|
| F1 | REST interface against a published data contract | `services/gateway` |
| F2, D1 | Schema/range/category/cross-field validation at the ingestion boundary; quarantine | Gateway + `quarantine` table |
| F8 | Health, metrics, structured logs for the whole pipeline | All services (via `orcas_common`) |
| A4 | FastAPI async, run id + polling, queue between submit and execute; justify | Gateway + Celery/Redis wiring |
| A6 | Dashboard: success rate, steps/tool calls per run, latency e2e + per step, retry rate, uptime | Grafana |
| A7 | `--scale worker=3` works; measure; find the bottleneck | Load tests |
| D3 | ≥4 services, single `compose up`, health checks, dependencies, boundary justification | Compose |
| D4 | No credentials/endpoints/model ids in repo; `.env.example` | Config |
| D5 | Structured logging, correlation ID, OTel→Jaeger, Prometheus, Grafana, run reconstruction | Observability stack |
| D6 | SLO declared *before* measuring; Locust with stub; RPS/p50/p95; failure concurrency + component | `loadtest/` |
| D7 | Rate limiting per client; whole-run cache; measure effect on p95 and cost | Gateway |
| D8 | Runbook (2 failure modes) + incident log | `docs/` |
| L4 (input side) | Prompt-injection + toxicity checks on every free-text field *before* any model | Gateway |
| L6 (feedback) | Feedback endpoint, persisted, aggregated | Gateway + Postgres |

### Directories
```
docker-compose.yml, Makefile, .env.example, .github/ (CI)
contracts/                  # you are the *editor* of contracts/; changes need all three approvals
libs/orcas_common/          # config, JSON logging, correlation-ID middleware, OTel bootstrap, RunStore (Postgres + in-memory)
services/gateway/           # FastAPI
observability/              # prometheus.yml, otel-collector.yaml, Grafana dashboards (JSON, provisioned)
loadtest/                   # locustfile.py, results/
docs/                       # slo.md, runbook.md, incident_log.md, data_contract.md
tests/integration/, tests/e2e/
```

## 2. Interfaces

### You provide
| Interface | Consumer | Delivered |
|---|---|---|
| `contracts/*` frozen JSON Schemas + OpenAPI skeletons | everyone | **Day 3** |
| `orcas_common` v0: config loader, JSON logger, correlation-ID middleware, `RunStore` interface + **in-memory implementation** | Mitesh, Tahir | **Day 5** |
| Compose skeleton with all services as placeholders and health checks | everyone | **Day 5** |
| `POST /v1/complaints`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/trace`, `POST /v1/runs/{id}/feedback`, `GET /health`, `/ready`, `/metrics`, `GET /v1/admin/quarantine` | web client, eval, demo | v0 Day 10 |
| Celery queue + `orcas.run_complaint` dispatch | Mitesh's worker | Day 8 |
| Postgres schema (`runs, run_events, quarantine, feedback, eval_scores`) + migrations | everyone | Day 8 |

### You consume
| Interface | From | Mock/fallback |
|---|---|---|
| Celery task `orcas.run_complaint(run_id)` implementation | Mitesh | **Write a fake worker** (sleeps 3s, writes a canned valid determination). All your gateway and load-test work runs against it. |
| Retrieval `/metrics`, `/health`, `/v1/index/info` | Tahir | Placeholder container returning 200 until Day 12 |
| Metric names, event kinds | Mitesh | Agree on a list by Day 5 |
| Sample valid/malformed payloads | Tahir | Write your own first; Tahir supplies richer ones by Day 6 |

> **Independence rule:** gateway, queue, compose, observability and load tests must work with the fake worker + `llm_stub` and no real LLM and no real index.

## 3. Design you are expected to implement

### 3.1 Data contract and validation (F1, F2, D1)
Publish `contracts/complaint.v1.schema.json` and `docs/data_contract.md`. Proposed fields:

| Field | Type / rule |
|---|---|
| `client_id` | string 3–64, `[A-Za-z0-9_-]` (required) |
| `is_synthetic` | boolean, **must be true** (enforces the out-of-scope rule) |
| `channel` | enum `web, email, phone, branch` |
| `text` | string 30–4000 chars, printable, no control chars |
| `date_of_event` | date, not in the future |
| `date_filed` | date, **≥ `date_of_event`**, not in the future (cross-field) |
| `amount_disputed_inr` | optional number 0–1,000,000,000 |
| `entity_name_hint` | optional string ≤ 120 |
| `prior_complaint_to_entity` | optional boolean; if true then `prior_complaint_date` required and between `date_of_event` and `date_filed` (cross-field) |

Pydantic v2 models generated from or checked against the JSON Schema (a test fails if they drift). Validation happens **before enqueue**. A malformed request returns 422 with field-level errors, writes to `quarantine`, and **creates no run row and no queue message**; add a test proving this (demo item 4 evidence).

### 3.2 Input guardrails (L4)
Run on `text` and `entity_name_hint` before enqueue: injection heuristics (instruction-override patterns, role-play, delimiter abuse) plus a lightweight classifier or rule-based scorer; toxicity check. Flagged → quarantine with reason `guardrail:injection` / `guardrail:toxicity`, HTTP 422, no run. Keep a labelled test list of ≥20 attack strings and ≥20 benign complaints, and report the false-positive rate (a regulator complaint can legitimately be angry; do not block anger, block instructions).

### 3.3 Async execution (A4)
`POST` returns `202 {run_id, status_url}` in milliseconds; Celery worker executes; status values `queued|running|completed|escalated|failed`. `acks_late`, visibility timeout, task time limit, idempotent restart keyed by `run_id`. Justification for the report: runs take tens of seconds, which exceeds sensible HTTP timeouts; queue absorbs bursts and makes worker scaling independent of intake.

### 3.4 Compose (D3, D4)
Services: `gateway, worker, retrieval, llm-gateway, llm-stub (profile), ingest (one-shot), web, redis, postgres, prometheus, grafana, otel-collector, jaeger, cadvisor`. Every service has `healthcheck`; `depends_on: condition: service_healthy`; `worker` must have no `container_name` or fixed host port (required for `--scale`). Embedding model baked into the retrieval image at build time. Corpus PDFs are in the repo, so a clean clone works without downloads except Docker images and the LLM API. `.env.example` documents every variable; CI greps for secrets/model ids. Write the **deployment-boundary justification** in `docs/ARCHITECTURE.md §2.1` (draft exists; update to match reality).

### 3.5 Observability (D5, A6, F8)
- JSON logs with `correlation_id` on every line in every service (shared lib; worker and retrieval use the same helpers).
- OpenTelemetry in gateway, worker, retrieval, llm-gateway → collector → Jaeger. Verify one trace shows gateway → queue → worker → retrieval → llm-gateway.
- Prometheus scrapes every service. Grafana dashboards **provisioned from files** so a clean clone has them: p50/p95, error rate, throughput, availability, CPU/memory per container, agent KPIs (A6), tokens and cost by step (L5), cache hit ratio, queue depth, breaker state, index freshness, quality over time (L6).
- `GET /v1/runs/{id}/trace`: reconstruct every tool call, query, passage, prompt version, per-step latency and cost, and verification outcome from `run_events`.

### 3.6 SLO, load test, scaling (D6, A7)
1. **By Day 24, commit `docs/slo.md`** with the p95 target, *before running any load test*. The git timestamp is your evidence. Proposal to challenge, not a decision: *p95 end-to-end ≤ 45 s at 10 concurrent submitters, stub delay 1.5 s per LLM call, ~6 calls per run, 1 worker*, justified against §1: grievance routing is not real-time, but a human officer or investor waiting on a routing answer tolerates tens of seconds, not minutes. Pick numbers you can defend.
2. Locust user: submit → poll until terminal state. Measure **submission-to-final-result**, not the POST.
3. Ramp concurrency; record RPS, p50, p95; find the concurrency where the target fails and **name the component** from Grafana (worker pool saturation, retrieval CPU, Postgres writes, Redis).
4. Repeat with `--scale worker=3`. If p95 doesn't improve, find the real bottleneck rather than reporting a null result.
5. Report cost per 100 runs from L5 data (coordinate with Mitesh).

### 3.7 Caching and rate limiting (D7)
- **Run cache:** key = hash(normalised complaint text + relevant fields + `index_version` + prompt versions); TTL; bypass flag for evaluation. Justification: duplicate submissions are common in complaint systems, and re-running a multi-step agent is expensive. Measure hit ratio, p95 and cost-per-run effect.
- **Rate limiting:** per `client_id` token bucket in Redis; 429 with `Retry-After`; metrics on rejections. Justification: cost control against a looping client.

### 3.8 Feedback (L6)
`POST /v1/runs/{id}/feedback {rating: up|down, reason}` persisted in `feedback`; aggregation endpoint and a Grafana panel.

### 3.9 Runbook and incident log (D8)
`docs/runbook.md`: two most likely failures, each with **symptom → confirmation → recovery**. Likely candidates: (1) LLM provider outage / rate limiting (breaker open, `degraded` results rising); (2) worker backlog (queue depth rising, p95 breached) or index stale/empty (retrieval returning nothing). `docs/incident_log.md`: **only genuine entries** from real failures during development. Everyone appends as incidents happen (what happened, detection, cause, fix). Don't fabricate an entry; if you reach Day 38 with none, break something deliberately and say so honestly.

## 4. Day-by-day plan (50-day budget)

| Days | Work | Done when |
|---|---|---|
| 1–3 | Contracts: complaint, run status, determination skeletons; repo skeleton, Makefile, CI lint/test. | Contracts frozen D3 |
| 4–5 | `orcas_common` v0 (config, JSON logs, correlation ID, `RunStore` in-memory). Compose skeleton, `.env.example`. | Mitesh and Tahir can import it and `compose up` shows placeholders healthy |
| 6–10 | Gateway v0: validation + quarantine + Postgres + Celery enqueue + status endpoint. Fake worker. Unit tests for contract. | Valid payload → run completes via fake worker; bad payload quarantined with no run |
| 11–14 | **Walking skeleton:** swap in real worker and retrieval; fix compose ordering; clean-clone test on a second machine. | **Day 14: valid request works end-to-end from a clean clone** |
| 15–22 | OTel + collector + Jaeger; Prometheus metrics in all services; Grafana v1; trace reconstruction endpoint; input guardrails; rate limiting; feedback endpoint. | One run visible in Jaeger across all services; reconstruct from `run_id` |
| 23–24 | **Write and commit `docs/slo.md`** | Committed before any load test |
| 25–30 | Locust + stub integration; baseline; find failure concurrency. | Locust report vs target |
| 31–36 | `--scale worker=3` experiment and bottleneck analysis; run cache + effect on p95/cost; Grafana A6, L5, L6 panels. | Scaling and cache numbers recorded |
| 37–40 | Runbook, incident log curation, README tested by someone unfamiliar; chaos checks (kill worker, kill redis, kill retrieval) and fix findings. | A teammate runs README cold |
| 41–43 | Report sections (architecture, boundaries, SLO, results, cost, what didn't work). Freeze. | Report + repo due |
| 44–50 | Rehearsal of all 10 demo items with a stopwatch; bug fixes only. | Dress rehearsal passes twice |

## 5. Definition of done
1. `docker compose up` from a clean clone. 2. Tests (contract, validation, integration). 3. Metrics on `/metrics`. 4. Structured logs with correlation ID. 5. Documented in `docs/work/notes_jignas.md` (running log).

## 6. Demo items you drive
- **#1** compose up from clean clone
- **#3** end-to-end trace (Jaeger + trace endpoint)
- **#4** malformed request rejected + quarantine row + proof no run started
- **#7** input-side injection rejection
- **#9** Locust results vs declared target, failure concurrency, `--scale` effect

## 7. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Compose startup flaky (ordering, health checks, slow model load) | Health checks with generous `start_period`; test from a clean clone every week; `make doctor` script |
| You become a single point of failure for everyone's integration | Contracts frozen early; `orcas_common` kept tiny; any teammate may fix compose with a PR |
| Observability is huge and eats time | Order: logs + correlation → Prometheus → Grafana essentials → OTel/Jaeger → polish. Cut dashboards before cutting logging |
| Load test measures the wrong thing | Measure submit→terminal; use stub; fix workload shape in `slo.md` |
| SLO set after seeing results | Commit `slo.md` Day 24; it is checked by git history |
| Scaling shows no gain | Expected possible (stub, single Postgres, GIL, Celery prefetch). Investigate and report the real bottleneck |

## 8. Handoffs you must give others
| To | What | By day |
|---|---|---|
| All | Frozen contracts | 3 |
| Mitesh, Tahir | `orcas_common` v0 + compose skeleton | 5 |
| Mitesh | Celery wiring + Postgres schema | 8 |
| Tahir | Gateway API running (fake worker) for the web client | 10 |
| Tahir | Grafana panel for quality over time (needs `eval_scores`) | 36 |
| All | Report sections | 42 |
