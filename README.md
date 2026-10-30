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
Prometheus, Grafana, OpenTelemetry, and Jaeger are configured for run inspection.
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
| Observability | JSON logs, run events, OpenTelemetry/Jaeger, Prometheus, Grafana |
| Load test | Locust measures submit-to-terminal latency; [measured results](docs/work/jignas/report-sections.md) |
| Packaging | Docker Compose |

## 4. Setup / installation

### Prerequisites
- Docker Engine ≥ 24 and Docker Compose v2
- ~8 GB free RAM, ~5 GB disk
- No LLM API key is needed for the initial demo; the stub is enabled by default
- Set `ORCAS_ADMIN_TOKEN` in `.env` to inspect quarantine and feedback totals
- Python 3.11 is only needed if you want to run CLIs/tests outside Docker

### Run it

```bash
git clone https://github.com/Mitxh13/orcas.git
cd orcas

cp .env.example .env
docker compose up --build -d
```

On Windows PowerShell, use `Copy-Item .env.example .env` for the copy step. The first start builds the corpus index through the `ingest` one-shot service and creates the database tables through `migrate`. Wait until `docker compose ps` shows the long-running services healthy. Run `make doctor` or `python scripts/doctor.py` to check readiness. See the [demo guide](docs/initial_demo.md) for the valid request and quarantine proof.

| What | URL |
|---|---|
| Web client | http://localhost:8080 |
| Gateway API docs (OpenAPI) | http://localhost:8000/docs |
| Retrieval API | http://localhost:8001/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Jaeger | http://localhost:16686 |

### Variants

```bash
# The default .env enables the fixed-response LLM stub.
# Scale agent workers later when measuring throughput:
docker compose up --scale worker=3
```

### Try the reference case

```bash
python scripts/demo/initial_demo.py
```

### Evaluate and test

```bash
make eval        # batch evaluation (>=20 scenarios), prints groundedness / context relevance / faithfulness / hallucination rate
make test        # unit and contract tests
make doctor      # Compose configuration and running service readiness
make loadtest    # stub-backed end-to-end Locust workload
```

## 5. Repository structure

```
orcas/
├── README.md
├── Makefile
├── docker-compose.yml            # application and observability services
├── .env.example                  # every config variable, no secrets
├── contracts/                    # FROZEN interfaces: JSON Schemas + OpenAPI (single source of truth)
├── libs/orcas_common/            # logging, config, and shared run storage
├── services/
│   ├── gateway/                  # FastAPI intake, validation, quarantine, queue
│   ├── metrics_exporter/         # persisted run and queue metrics
│   ├── worker/                   # LangGraph agents (Researcher + Verifier), tools, resilience  (scalable)
│   ├── llm_gateway/              # provider adapters, circuit breaker, backoff, fallback, token/cost accounting
│   ├── llm_stub/                 # fixed-response LLM with configurable delay (load tests)
│   ├── retrieval/                # search / clause lookup / chunk resolve API over Chroma
│   ├── ingest/                   # idempotent corpus pipeline CLI (parse → chunk → embed → index)
│   └── web/                      # minimal web client
├── corpus/                       # raw PDFs + MANIFEST.yaml (source URL, checksum, version, date)
├── prompts/                      # versioned prompt artifacts (YAML), compared in evaluation
├── eval/                         # scenarios.yaml (>=20), run_eval.py, live scorer, results/
├── loadtest/                     # scenario payloads and Locust driver
├── observability/               # collector, Prometheus, provisioned Grafana
├── scripts/                      # helper scripts
├── tests/                        # service and platform tests
└── docs/
    ├── ARCHITECTURE.md
    ├── PROJECT_DOCUMENTATION.md
    ├── initial_demo.md
    ├── data_contract.md
    ├── slo.md
    ├── runbook.md
    ├── incident_log.md
    ├── decisions/                # ADRs (one file per decision)
    └── work/                     # MITESH.md · TAHIR.md · JIGNAS.md
```

## 6. Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Complete project documentation](docs/PROJECT_DOCUMENTATION.md)
- Work breakdown: [Mitesh](docs/work/MITESH.md) · [Tahir](docs/work/TAHIR.md) · [Jignas](docs/work/JIGNAS.md)
- [Initial demo](docs/initial_demo.md) and [data contract](docs/data_contract.md)

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

