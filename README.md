# ORCAS

**Orchestrated Regulatory Complaint Analysis & Routing System**

> A multi-agent, production-style service that reads a free-text investor complaint, decides *which regulator* (SEBI or RBI) and *which regulated entity* is responsible, and routes it with verified, circular-level citations. When it cannot be sure, it escalates to a human instead of guessing.

Course project: **UE24AM342AA5 – ML System Design & AgentOps** (PES University, Dept. of CSE – AI & ML) · Mini Project 12 · Team of 3.

> ⚠️ **Synthetic data only.** ORCAS never submits anything to a real regulator portal. It never *disposes* of a complaint; it produces a routing determination with evidence, and a grievance officer decides.

---

## 1. The problem

Retail investors write to SEBI about brokers, mutual funds, registrars, listed companies and depositories. But not every complaint written to SEBI belongs to SEBI. A bank fixed-deposit complaint belongs to the **RBI Integrated Ombudsman Scheme**, even if it is full of investment vocabulary and says "I want to complain to SEBI".

A single retrieval pass returns SEBI's grievance framework for that complaint, which is wrong. ORCAS uses an **agent** that:

1. retrieves the provision that appears to apply,
2. asks whether another provision modifies or excludes it, and searches again using what it has just read,
3. **verifies** that every claim and citation is supported by retrieved text,
4. retries with a different strategy, or **escalates**, if verification fails.

### The reference case (must always work)

> *"I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI."*
> Expected: **refer-to-other-regulator → RBI Integrated Ombudsman Scheme**, with citations that resolve to retrieved text.

## 2. How it works (30-second version)

```
Web client ─▶ Gateway (FastAPI) ─▶ validate / guardrails / quarantine ─▶ Redis queue
                                                                            │
                         Postgres (runs, events, feedback) ◀── Agent worker (LangGraph, ×N)
                                                                 │   Researcher agent ⇄ Verifier agent
                                                                 ├──▶ Retrieval service (Chroma + MiniLM)
                                                                 └──▶ LLM gateway (provider / breaker / cost)
Prometheus + Grafana + OpenTelemetry/Jaeger observe everything.
```

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 3. Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.11 |
| Agent orchestration | LangGraph (two agents, tool-calling loop) |
| Retrieval orchestration | LangChain |
| Vector store | Chroma (persistent, upsert-by-id so re-embedding is incremental) |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2`, local, no API cost |
| API | FastAPI + Pydantic v2 |
| Async execution | Celery + Redis |
| State | PostgreSQL |
| LLM access | Own `llm-gateway` service (LiteLLM adapters, provider chosen by env var) |
| Evaluation | TruLens + custom claim-level hallucination metric |
| Observability | Structured JSON logs, OpenTelemetry → Jaeger, Prometheus, Grafana, cAdvisor |
| Load test | Locust against an LLM stub service |
| Packaging | Docker Compose |

## 4. Setup / installation

### Prerequisites
- Docker Engine ≥ 24 and Docker Compose v2
- ~8 GB free RAM, ~5 GB disk
- A free-tier LLM API key (any provider supported by LiteLLM), **or** use the stub profile (no key)
- Python 3.11 is only needed if you want to run CLIs/tests outside Docker

### Run it

```bash
git clone https://github.com/<org>/orcas.git
cd orcas

cp .env.example .env
# edit .env: set LLM_PROVIDER, LLM_MODEL, LLM_API_KEY (see .env.example for every variable)

docker compose up --build
```

The first start builds the corpus index automatically through the idempotent `ingest` one-shot service. Wait until `docker compose ps` shows every service `healthy`.

| What | URL |
|---|---|
| Web client | http://localhost:8080 |
| Gateway API docs (OpenAPI) | http://localhost:8000/docs |
| Grafana (dashboards) | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Jaeger (traces) | http://localhost:16686 |

### Variants

```bash
# No LLM key, fixed-response stub with realistic delay (used for load tests)
docker compose --profile stub up --build

# Scale agent workers
docker compose up --scale worker=3

# Rebuild / refresh the index manually (idempotent)
docker compose run --rm ingest orcas-ingest --corpus /corpus --reindex-if-changed

# Re-embed with a new embedding model without rebuilding everything
docker compose run --rm ingest orcas-ingest --re-embed --model-tag <new-tag>
```

### Try the reference case

```bash
curl -s -X POST http://localhost:8000/v1/complaints \
  -H 'Content-Type: application/json' \
  -d '{
    "client_id": "demo-client",
    "is_synthetic": true,
    "channel": "web",
    "date_of_event": "2026-08-01",
    "date_filed": "2026-09-01",
    "text": "I invested in a fixed deposit through my bank app. The interest credited is lower than what I was promised. I want to complain to SEBI."
  }'
# -> {"run_id":"...","status_url":"/v1/runs/..."}

curl -s http://localhost:8000/v1/runs/<run_id>
```

### Evaluate, load test, test

```bash
make eval        # batch evaluation (>=20 scenarios), prints groundedness / context relevance / faithfulness / hallucination rate
make loadtest    # Locust against the stub profile; compare to the SLO in docs/slo.md
make test        # unit + contract + integration tests
```

## 5. Repository structure

```
orcas/
├── README.md
├── Makefile
├── docker-compose.yml            # all services, health checks, profiles (stub)
├── .env.example                  # every config variable, no secrets
├── contracts/                    # FROZEN interfaces: JSON Schemas + OpenAPI (single source of truth)
├── libs/orcas_common/            # logging, correlation-id, OTel setup, config, run-store (tiny, frozen API)
├── services/
│   ├── gateway/                  # FastAPI ingestion boundary, validation, quarantine, guardrails, rate limit, queue
│   ├── worker/                   # LangGraph agents (Researcher + Verifier), tools, resilience  (scalable)
│   ├── llm_gateway/              # provider adapters, circuit breaker, backoff, fallback, token/cost accounting
│   ├── llm_stub/                 # fixed-response LLM with configurable delay (load tests)
│   ├── retrieval/                # search / clause lookup / chunk resolve API over Chroma
│   ├── ingest/                   # idempotent corpus pipeline CLI (parse → chunk → embed → index)
│   └── web/                      # minimal web client
├── corpus/                       # raw PDFs + MANIFEST.yaml (source URL, checksum, version, date)
├── prompts/                      # versioned prompt artifacts (YAML), compared in evaluation
├── eval/                         # scenarios.yaml (>=20), run_eval.py, live scorer, results/
├── loadtest/                     # locustfile.py, SLO, results
├── observability/                # prometheus.yml, otel-collector.yaml, grafana dashboards
├── scripts/                      # helper scripts
├── tests/                        # integration + e2e
└── docs/
    ├── ARCHITECTURE.md
    ├── PROJECT_DOCUMENTATION.md
    ├── slo.md
    ├── runbook.md
    ├── incident_log.md
    ├── data_contract.md
    ├── decisions/                # ADRs (one file per decision)
    └── work/                     # MITESH.md · TAHIR.md · JIGNAS.md
```

## 6. Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Complete project documentation](docs/PROJECT_DOCUMENTATION.md)
- Work breakdown: [Mitesh](docs/work/MITESH.md) · [Tahir](docs/work/TAHIR.md) · [Jignas](docs/work/JIGNAS.md)
- Operations: `docs/runbook.md`, `docs/incident_log.md`, `docs/slo.md`

## 7. Reference links

**Regulatory sources** *(navigate to the current versions; exact documents and versions are pinned in `corpus/MANIFEST.yaml`)*
- SEBI: https://www.sebi.gov.in/ (Legal → Circulars / Master Circulars / Regulations)
- RBI: https://www.rbi.org.in/ (Integrated Ombudsman Scheme)
- RBI complaint portal (for reading only, never submit): https://cms.rbi.org.in/

**Tools & docs**
- LangGraph: https://langchain-ai.github.io/langgraph/
- LangChain: https://python.langchain.com/
- Chroma: https://docs.trychroma.com/
- Sentence-Transformers: https://www.sbert.net/
- FastAPI: https://fastapi.tiangolo.com/
- Celery: https://docs.celeryq.dev/
- LiteLLM: https://docs.litellm.ai/
- TruLens: https://www.trulens.org/
- OpenTelemetry (Python): https://opentelemetry.io/docs/languages/python/
- Jaeger: https://www.jaegertracing.io/docs/
- Prometheus: https://prometheus.io/docs/
- Grafana: https://grafana.com/docs/
- Locust: https://docs.locust.io/
- Docker Compose: https://docs.docker.com/compose/

## 8. Contributors

| Name | Area | Details |
|---|---|---|
| **Mitesh** | Agent core & LLM layer: LangGraph agents, verification, resilience, prompts, LLM gateway, output guardrails, cost tracking | [`docs/work/MITESH.md`](docs/work/MITESH.md) |
| **Tahir** | Data & quality: corpus, ingestion, retrieval service, evaluation, live quality scoring, web client | [`docs/work/TAHIR.md`](docs/work/TAHIR.md) |
| **Jignas** | Platform & reliability: gateway, validation & quarantine, queue, Compose, observability, load testing, scaling, runbook | [`docs/work/JIGNAS.md`](docs/work/JIGNAS.md) |

### Contribution rules
- Branch per task: `feat/<owner>/<topic>`; PRs into `main`; CI must pass.
- `contracts/` changes need an ADR in `docs/decisions/` and approval from **all three** members.
- Never commit secrets, endpoints or model identifiers. Everything environment-specific goes in `.env`.

