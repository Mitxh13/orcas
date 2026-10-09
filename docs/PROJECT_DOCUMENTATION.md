# ORCAS: Complete Project Documentation

Course: UE24AM342AA5 ML System Design & AgentOps · Mini Project 12 · Team: Mitesh, Tahir, Jignas · Budget: 50 days

Related: [Architecture](ARCHITECTURE.md) · [Mitesh](work/MITESH.md) · [Tahir](work/TAHIR.md) · [Jignas](work/JIGNAS.md)

---

## 1. Overview and scope

**What ORCAS is.** A multi-agent service that accepts a synthetic retail-investor complaint, validates it, determines jurisdiction (SEBI vs RBI) and the responsible regulated entity from retrieved regulations, and returns a routing determination with circular-level citations, or escalates to a human.

**What it never does.** It never disposes of a complaint, never grants relief, never submits to a real portal. A grievance officer decides.

**In scope:** F1–F8, the 22 platform requirements (A1–A8, L1–L6, D1–D8) subject to prioritisation (§14), the 10 demo items.
**Out of scope:** model training, cloud deployment, real regulator integration, real personal data.

### 1.1 Success criteria
1. `docker compose up` from a clean clone serves a valid request (**gate: zero marks otherwise**).
2. The §4.1 bank-FD case routes to RBI with citations that resolve to retrieved text.
3. Cases the corpus can't answer produce escalation, not fabrication.
4. All ten demo items run live.

## 2. Problem background

Two decisions per complaint:
1. **Jurisdiction.** Does this belong to SEBI at all? A bank-end payment failure or bank deposit complaint belongs to the RBI Integrated Ombudsman Scheme.
2. **Responsible entity.** Broker, mutual fund, registrar, listed company or depository, which fixes the redressal timeline. One complaint can involve two entities and sometimes two regulators → escalate.

**Why it's hard.** Each framework states what it covers, not what it excludes. A complaint using investment vocabulary about a bank product retrieves the wrong framework on a single embedding search. The deciding provision is one the complaint never mentions.

### 2.1 The reference case (§4.1)
> "I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI."

Required reasoning chain: (1) first search returns SEBI grievance framework (appears to apply) → (2) agent extracts *product = bank fixed deposit, entity = bank* and asks what covers it → (3) searches again, framework-filtered to RBI, using terms from what it read → (4) finds RBI Integrated Ombudsman Scheme provisions covering regulated entities (banks) and the relevant ground for complaint → (5) drafts `refer_to_other_regulator → RBI` → (6) verifier confirms each claim and citation → (7) result with citations.

**Unverified:** exact clause numbers and wording of the SEBI and RBI passages. Tahir pins them from the downloaded documents by Day 8 and they become the reference answer at Evaluation 1. Do not write clause numbers into prompts or docs from memory.

## 3. Requirements traceability

Owner key: **M** = Mitesh, **T** = Tahir, **J** = Jignas.

### 3.1 Functional

| ID | Requirement | Owner | Component | Evidence |
|---|---|---|---|---|
| F1 | REST interface, published data contract | J | gateway, `contracts/` | OpenAPI at `/docs`, `docs/data_contract.md` |
| F2 | Validation at ingestion boundary, quarantine | J | gateway | Demo #4, quarantine rows, test that no run is created |
| F3 | Classify domain (broker, MF, registrar, listed co., depository, out-of-jurisdiction) | M | Researcher `classify` | Eval accuracy |
| F4 | Retrieve jurisdictional provisions + servicing obligations | T | retrieval | recall@k |
| F5 | Grounded determination with circular-level citations | M | Researcher + Verifier | Groundedness, demo #2 |
| F6 | Route: assign-with-timeline / refer-other-regulator / not-maintainable, with basis | M | Researcher `draft` | Route accuracy |
| F7 | Escalate on split regulators or out-of-corpus entity | M | Verifier + rules | Demo #5, escalation P/R |
| F8 | Health, metrics, structured logs | J (all) | `orcas_common` | `/health`, `/metrics`, logs |

### 3.2 Agent system (Unit 4)

| ID | Requirement | Owner | Component |
|---|---|---|---|
| A1 | LangGraph; retrieval is a tool; ≥3 tools; bounded loop | M | worker |
| A2 | ≥2 agents, distinct duties, hand-off, failure documented | M | Researcher, Verifier |
| A3 | Verify before returning; citations resolve; else retry/escalate | M | Verifier |
| A4 | FastAPI async, run id, polling, queue, justified | J | gateway + Celery |
| A5 | Timeouts, backoff, breaker, fallback; no confident incomplete answers | M | llm-gateway, worker |
| A6 | Per-agent KPIs on dashboard | M emits, J displays | Grafana |
| A7 | `--scale` works; measured; bottleneck | J | compose, Locust |
| A8 | Simple web client | T | web |

### 3.3 LLMOps (Unit 3)

| ID | Requirement | Owner | Component |
|---|---|---|---|
| L1 | LangChain + Chroma; documented chunking; re-embed without rebuild | T | ingest, retrieval |
| L2 | Versioned prompts; ≥2 variants compared | M (prompts), T (harness) | `prompts/`, `eval/` |
| L3 | ≥20 scenarios; TruLens; 4 metrics; before/after | T | `eval/` |
| L4 | Injection defence, output filter, toxicity in/out | J (input), M (output, isolation) | gateway, worker |
| L5 | Tokens + cost per step/run; cost per 100 runs; top driver | M records, J displays | llm-gateway |
| L6 | Feedback endpoint; live sampled scoring; chart | J (endpoint), T (scoring) | gateway, eval |

### 3.4 Platform (Units 1–2)

| ID | Requirement | Owner | Component |
|---|---|---|---|
| D1 | Contracts; validation; quarantine | J | gateway |
| D2 | Idempotent ingest CLI; freshness + throughput metrics | T | ingest |
| D3 | ≥4 services, one `compose up`, health checks, boundary justification | J | compose |
| D4 | No secrets/endpoints/model ids; `.env.example` | J (all) | config, CI |
| D5 | Logs, correlation ID, OTel→Jaeger, Prometheus, Grafana, reconstruct by run id | J | observability |
| D6 | SLO before measuring; Locust + stub; RPS/p50/p95; failure concurrency | J (stub: M) | loadtest |
| D7 | Caching + rate limiting, measured | J (run cache, rate limit), T (retrieval cache) | gateway, retrieval |
| D8 | Runbook (2 failures) + genuine incident log | J (all append) | docs |

## 4. Data contracts

Contracts live in `contracts/` and are the single source of truth. A test fails if Pydantic models drift from the JSON Schemas.

### 4.1 Complaint (request) `complaint.v1`
See field table in `work/JIGNAS.md §3.1`. Cross-field rules: `date_filed ≥ date_of_event`; `prior_complaint_date` required iff `prior_complaint_to_entity = true`; `is_synthetic` must be true.

### 4.2 Determination (response) `determination.v1`

```json
{
  "run_id": "uuid",
  "status": "completed | escalated | failed",
  "route": "assign_to_entity | refer_to_other_regulator | not_maintainable | escalate_to_human",
  "regulator": "SEBI | RBI | UNDETERMINED",
  "entity_type": "broker | mutual_fund | registrar | listed_company | depository | bank | other | unknown",
  "timeline": { "days": 30, "basis_citation_id": "c2" },
  "basis": "plain-language justification, each claim tagged with citation ids",
  "citations": [
    { "citation_id": "c1", "doc_id": "...", "doc_title": "...", "circular_ref": "...",
      "clause": "...", "chunk_id": "...", "quote_span": "exact text found in chunk" }
  ],
  "escalation": { "reason_code": "split_regulators | out_of_corpus | unverified | budget_exhausted | agent_failure | verification_unavailable | guardrail | low_confidence", "reason": "...", "partial_findings": [] },
  "verification": { "passed": true, "claims_checked": 5, "claims_supported": 5, "attempts": 1 },
  "prompt_versions": { "researcher_reflect": "v2", "verifier": "v1" },
  "index_version": "...",
  "degraded": false,
  "disposed": false
}
```
Invariants enforced by the output filter: `disposed` is always `false`; `route` is in the enum; every `citation.chunk_id` resolves; `verification.passed` is true unless `status ≠ completed`; `status = completed` requires `degraded = false` **or** an explicit fallback annotation.

### 4.3 Run status `run_status.v1`
`{run_id, status: queued|running|completed|escalated|failed, submitted_at, started_at?, finished_at?, result?: determination}`

### 4.4 Run event kinds (run store)
`run_started, llm_call, tool_call, retrieval, reflect, draft, verify, retry, escalate, guardrail, cache_hit, degraded, run_finished`. Each carries `run_id, seq, ts, agent, step, latency_ms` plus kind-specific payload (query, passages, tokens, cost, prompt_version).

### 4.5 Interface summary
See `ARCHITECTURE.md §11`.

## 5. Corpus and retrieval design

### 5.1 Corpus
| Document | Used for |
|---|---|
| SEBI master circular on investor grievance redressal (including matters covered) | SEBI jurisdiction, entity-wise obligations and timelines |
| SEBI (Mutual Funds) Regulations, investor servicing chapter | Mutual fund servicing obligations |
| RBI Integrated Ombudsman Scheme | RBI jurisdiction, regulated entities, grounds for complaint |

Pinned in `corpus/MANIFEST.yaml` (URL, version/date, SHA-256, text-layer flag). **Unverified assumptions to confirm in week 1:** the current version of each document; whether the SEBI master circular alone covers registrars, listed companies and depositories; whether a text layer exists. If an entity category cannot be supported from the corpus, F7 requires escalation for it, and the report must say so.

### 5.2 Chunking and embedding
See `ARCHITECTURE.md §5` and `work/TAHIR.md §3.3`. Summary: clause-aligned chunks with heading-path metadata; scope cards as finding aids; local MiniLM embeddings; Chroma with content-hash IDs; hybrid retrieval pending A/B.

### 5.3 Idempotency and re-embedding
Chunk id = hash of (doc, clause, text, model tag). Re-run = no change. `--re-embed --model-tag X` replaces vectors for that tag by id.

### 5.4 Freshness
`index_info`: built_at, per-doc version, chunk count, model tag, ingest throughput; metric `orcas_index_built_timestamp`, `orcas_index_chunks`.

## 6. Agent design

### 6.1 Topology
Two agents, Researcher and Verifier, with a defined payload hand-off. See `ARCHITECTURE.md §4`.

### 6.2 Tools
| Tool | Purpose | Agent |
|---|---|---|
| `search_corpus` | Semantic/hybrid search with `framework`, `entity_type` filters | Researcher |
| `get_clause` | Fetch a clause by id (cross-reference following) | Researcher |
| `get_complaint_fields` | Read the request's own fields | Researcher |
| `list_framework_scope` | Entry points via scope cards (finding aid) | Researcher |
| `resolve_citation` | Confirm chunk exists and quote span is in it | Verifier |

### 6.3 Loop limits
`MAX_STEPS`, `MAX_TOOL_CALLS`, `RUN_WALL_CLOCK_S`, `MAX_VERIFY_RETRIES=2`; enforced in code outside the model. Defaults set from evaluation.

### 6.4 Verification algorithm
1. Decompose the draft into atomic claims, each linked to citation ids.
2. For each claim: supported / unsupported / contradicted given cited passages.
3. For each citation: `chunk_id` resolves; `quote_span` is a substring of the chunk text.
4. Check route ↔ evidence consistency and enum validity.
5. Decide: accept; retry with next strategy; escalate.
Fail-safe: any exception in verification → escalate.

### 6.5 Escalation reason codes
See determination schema. Every escalation includes partial findings so the officer doesn't start from zero.

## 7. LLMOps

### 7.1 Prompt management (L2)
`prompts/<name>/<version>.yaml` with `id, version, template, variables, changelog`. Loader records versions per response. Variants compared on the same set; the report states the winner, the metric and the delta. If the difference is within noise (small eval set), say so.

### 7.2 Evaluation (L3)
Set of ≥20 scenarios with labels and supporting passages (composition in `work/TAHIR.md §3.5`). Metrics: groundedness, context relevance, faithfulness, hallucination rate (definitions per the course glossary; computation documented in the report), plus route accuracy and escalation precision/recall computed from labels. Batch CLI: `make eval`. Deliberate-change experiment with before/after table.

**Honesty notes for the report:** LLM-judge metrics are noisy and groundedness and faithfulness will be highly correlated; 20 scenarios give wide confidence intervals; hold out ≥5 scenarios from tuning.

### 7.3 Guardrails (L4)
| Layer | Mechanism |
|---|---|
| Input | Injection heuristics + classifier; toxicity; length/charset; quarantine |
| Prompt | Untrusted text delimited as data; no instructions from data; tool args validated |
| Tools | Read-only |
| Output | Enum-restricted routes; `disposed=false`; forbidden-phrase filter; output toxicity |

### 7.4 Cost tracking (L5)
llm-gateway records prompt tokens, completion tokens and cost at call time per (run, agent, step). Report: cost per 100 runs and the largest driver (likely the Verifier's claim-check or the research loop; to be measured, not assumed). Free-tier cost may be reported as "equivalent list-price cost".

### 7.5 Feedback and live quality (L6)
Feedback endpoint (J), aggregation, live scorer samples N% of completed runs (T), Grafana "quality over time".

## 8. Platform

### 8.1 Services
`gateway, worker, retrieval, llm-gateway, llm-stub, ingest, web, redis, postgres, prometheus, grafana, otel-collector, jaeger, cadvisor` (14 containers; ≥4 required). Boundary justification in `ARCHITECTURE.md §2.1`.

### 8.2 Configuration (D4)
All config via env. Documented variables (in `.env.example`, no values for secrets): `LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_FALLBACK_MODEL, EMBED_MODEL, POSTGRES_*, REDIS_URL, MAX_STEPS, MAX_TOOL_CALLS, RUN_WALL_CLOCK_S, MAX_VERIFY_RETRIES, RATE_LIMIT_PER_MIN, RUN_CACHE_TTL_S, BREAKER_FAILURES, BREAKER_COOLDOWN_S, LIVE_SCORE_SAMPLE_RATE, OTEL_EXPORTER_OTLP_ENDPOINT`. CI fails on committed keys or hard-coded model names.

### 8.3 Caching (D7)
| Cache | Key | Justification |
|---|---|---|
| Embedding / retrieval | normalised query + filters + index_version | Repeated sub-queries across agent steps |
| Whole run | hash(normalised text + fields + index_version + prompt versions) | Duplicate complaints; the agent is the costly part |
Report: hit ratio; p95 and cost-per-run before/after.

### 8.4 Rate limiting (D7)
Per-client token bucket in Redis. Rationale: cost control against a looping client.

## 9. Reliability

### 9.1 Resilience (A5)
Timeouts on every model and tool call; bounded retry with exponential backoff and jitter; circuit breaker with state shared in Redis; fallback chain (secondary model → cached identical prompt → honest error); degraded runs flagged; incomplete runs escalate. Demo #8 by killing the provider.

### 9.2 SLO (D6)
Declared in `docs/slo.md` by **Day 24**, before any load test. Proposed starting point: p95 end-to-end ≤ 45 s at 10 concurrent submitters, stub 1.5 s/call. The team must justify the final number against §2 and the stub realism, and not tune it afterwards. Measure from submission to terminal status.

### 9.3 Load test plan
Locust, LLM replaced by stub; ramp 1 → 5 → 10 → 20 → 40 concurrent users; record RPS, p50, p95, error rate; find failure concurrency and responsible component; repeat with `--scale worker=3`; compare. Explain the bottleneck if scaling doesn't help.

### 9.4 Failure modes covered in the runbook
1. LLM provider outage or throttling. 2. Worker backlog / retrieval index empty or stale. (Final choice follows the incident log.)

## 10. Testing strategy

| Level | What | Owner |
|---|---|---|
| Unit | Validators, chunkers, agent nodes (mocked LLM/retrieval), output filter | each |
| Contract | Pydantic ↔ JSON Schema; OpenAPI conformance for gateway, retrieval, llm-gateway | J |
| Integration | Gateway → queue → fake worker; worker → real retrieval | J, M |
| End-to-end | Full compose, 4.1 case, escalation case, malformed, injection | J + all |
| Evaluation | `make eval` batch | T |
| Failure injection | Kill provider/worker/redis/retrieval | M, J |
| Clean-clone | Fresh machine follows README | J, weekly |

## 11. Demo plan (all ten items)

| # | Item | Lead | Command / script |
|---|---|---|---|
| 1 | Clean clone → working system | J | `docker compose up --build` |
| 2 | Valid request → routed result, citations resolve | T | Web client preset |
| 3 | End-to-end trace | J | Jaeger + `/v1/runs/{id}/trace` |
| 4 | Malformed → rejected + quarantined, no run | J | `scripts/demo/malformed.sh` |
| 5 | Unanswerable → escalation | M | `scripts/demo/out_of_corpus.sh` |
| 6 | Verifier catches unsupported answer | M | `ORCAS_INJECT_BAD_DRAFT=1` |
| 7 | Injection attempt blocked | J + M | `scripts/demo/injection.sh` |
| 8 | Breaker opens, degradation | M | `scripts/demo/kill_provider.sh` |
| 9 | Locust vs target; failure concurrency; `--scale` | J | `make loadtest` |
| 10 | §4.1 case | M + T | Web client preset "bank FD" |

## 12. Timeline (50 days)

> Assumption: report and repo are due **Day 43** (one week before the Day 50 presentation), and Evaluation 1 falls around Day 10–14. **Confirm the real dates with faculty this week** and shift milestones accordingly.

| Phase | Days | Goal |
|---|---|---|
| 0 Foundation | 1–5 | Contracts frozen (D3), repo skeleton, `orcas_common`, compose skeleton |
| 1 Walking skeleton | 6–14 | A valid request works end-to-end from a clean clone (**Day 14 gate**); Evaluation 1 package |
| 2 Core features | 15–30 | Verifier, multi-step research, 4.1 passes, ingestion polish, observability, guardrails, baseline eval |
| 3 Hardening | 31–40 | Resilience, SLO tests, scaling, caching, prompt comparison, before/after, runbook |
| 4 Freeze | 41–43 | Report and repo complete |
| 5 Rehearsal | 44–50 | Dress rehearsals only; bug fixes, no features |

### 12.1 Sync points
| Day | Sync | Output |
|---|---|---|
| 3 | Contract freeze | Signed-off `contracts/` |
| 5 | Shared lib + compose skeleton | Everyone unblocked |
| 14 | Walking-skeleton integration | End-to-end demo |
| 24 | SLO commit | `docs/slo.md` committed |
| 30 | Eval baseline + 4.1 passes | Numbers recorded |
| 36 | Feature complete | Everything demoable once |
| 43 | Freeze | Report + repo |
| 47 | Dress rehearsal #1 | Timed run of all 10 items |
| 49 | Dress rehearsal #2 | Clean |

Daily: 15-minute standup (what shipped, what blocks). Weekly: clean-clone test on a machine nobody developed on.

## 13. Collaboration rules

1. **Contracts first.** Interfaces frozen Day 3; any change needs an ADR (`docs/decisions/NNN-title.md`) and three approvals.
2. **Ownership.** Each member commits only in their directories; cross-directory changes via PR to the owner. CODEOWNERS enforces review.
3. **Mock everything you depend on.** Each work brief names the mock for each dependency.
4. **Branching.** `main` always runs (`docker compose up` works). Feature branches `feat/<owner>/<topic>`. Squash merge. CI runs lint, tests, secret scan.
5. **Running logs.** Each member keeps `docs/work/notes_<name>.md` (what tried, what failed, numbers). The "what did not work" report section is built from it, so failing to log costs marks.
6. **Incidents.** Any real failure → append to `docs/incident_log.md` the same day.
7. **No heroics after Day 43.** Only bug fixes.

## 14. Prioritisation (the assessment rewards sound prioritisation)

| Tier | Items | Reason |
|---|---|---|
| **0 (gate)** | Compose starts; valid request served | Zero marks otherwise |
| **1 (core)** | F1–F8, A1–A4, D1, D3, D4, L1, L3 (with 4.1), demo 2, 4, 5, 10 | Defines the project |
| **2 (strong)** | A5, A6, D2, D5, D6, L2, L4, L5, A7, A8 | Production-grade |
| **3 (polish)** | D7, D8, L6, hybrid retrieval, fallback provider | First to cut |

**Cut order if behind (by Day 30):** hybrid retrieval → fallback provider → L6 live scoring chart → whole-run cache (keep rate limiting) → prompt variant #3 → Grafana polish. Never cut: verification, escalation, quarantine-before-run, SLO-before-measurement.

## 15. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Compose doesn't start on demo machine | M | **Critical** | Weekly clean-clone tests; baked model; pinned image versions; offline-capable run with stub | J |
| R2 | Corpus can't support §4.1 reasoning | M | High | Confirm passages by Day 8; scope cards | T |
| R3 | Free-tier LLM rate limits / outage during demo | H | High | Stub profile; cached responses for demo; fallback model; record a backup video | M |
| R4 | Small models handle tool-calling badly | M | High | JSON action fallback | M |
| R5 | Verifier too lenient or too strict | M | High | Planted-bad-draft tests, tuned on eval | M |
| R6 | LLM-judge metrics noisy | H | Med | temp 0, cache, spot-check, label-based metrics | T |
| R7 | Integration slips | M | High | Day 14 gate; mocks; contracts frozen | all |
| R8 | One person overloaded | M | Med | Cut order; Jignas/Tahir take Mitesh's overflow (guardrail classifier, demo scripts) | all |
| R9 | SLO tuned after the fact | L | Med | Commit Day 24 | J |
| R10 | Spending overrun | L | Med | Account limit; loop bounds; rate limit; dev against stub | M |
| R11 | Scope creep after Day 30 | H | Med | Cut order; freeze Day 36 | all |
| R12 | Incident log empty at the end | M | Low | Log real failures as they happen; don't fabricate | all |

## 16. Decisions and rejected alternatives
See `ARCHITECTURE.md §12`; individual ADRs in `docs/decisions/`.

## 17. Open questions and unverified assumptions

1. Exact Evaluation 1 and final dates (assumed Days ~12 and 50).
2. Current versions/names of the three corpus documents; whether the SEBI master circular covers all five entity types.
3. Which free-tier provider and model; its rate limits and tool-calling quality.
4. Whether hybrid retrieval helps (decide by A/B).
5. Exact clause references for the §4.1 passages (Tahir pins by Day 8).
6. Final SLO numbers (Day 24).
7. Whether TruLens' feedback functions work with the chosen free-tier judge or need a custom provider wrapper.

## 18. Report outline (due Day 43)

1. Problem and scope · 2. Agent design (topology, tools, verification, loop limits) · 3. Architecture diagram with boundaries marked · 4. Design decisions and what was rejected · 5. Evaluation before/after · 6. SLO, measured results, bottleneck, cost per 100 runs · 7. What did not work.

## 19. Glossary
See `00_GLOSSARY.pdf` from the course. Terms used here: groundedness, context relevance, faithfulness, hallucination rate, prompt injection, data contract, ingestion boundary, quarantine, data freshness, deployment boundary, idempotent, correlation ID, SLO, p50/p95, RPS, circuit breaker, exponential backoff, stub service, graceful degradation.
