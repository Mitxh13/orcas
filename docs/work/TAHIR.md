# TAHIR: Data, Retrieval & Quality

**Role in one line:** you own the knowledge and the proof: corpus, ingestion, retrieval service, the evaluation set and metrics, live quality scoring, and the web client.

Your work decides whether the agent *can* find the provision that matters, and whether anyone can *prove* the system works. Evaluation 1 depends on you early.

---

## 1. What you own

### Requirements
| ID | Requirement | Your piece |
|---|---|---|
| F4 | Retrieve jurisdictional provisions + servicing obligations | Retrieval service |
| L1 | LangChain/LlamaIndex + FAISS/Chroma; documented chunking & embedding; re-embedding without full rebuild | Retrieval + ingest |
| D2 | Idempotent pipeline, CLI, freshness + throughput metrics | `services/ingest` |
| L3 | ≥20-scenario eval set, batch CLI, TruLens, 4 metrics, before/after a deliberate change | `eval/` |
| L2 (harness) | Run ≥2 prompt variants on the same eval set and report | `eval/` |
| L6 (scoring) | Auto-score sample of live runs; quality-over-time chart | `eval/live_scorer.py` |
| D7 (retrieval cache) | Cache embeddings/retrievals, measure effect | Retrieval service |
| A8 | Simple web client: submit, poll, show recommendation + citations + escalation path | `services/web` |
| Eval 1 | Corpus confirmation + project plan; the passages the §4.1 case depends on, and test values | Docs |

### Directories
```
corpus/                     # raw PDFs, MANIFEST.yaml (source URL, version/date, SHA-256, text-layer yes/no)
services/ingest/            # CLI: orcas-ingest (parse → segment → chunk → embed → index)
services/retrieval/         # FastAPI search / clause / chunk / index-info API
services/web/               # static web client
eval/                       # scenarios.yaml, run_eval.py, metrics.py, live_scorer.py, results/
tests/retrieval/            # retrieval unit tests, recall@k on the scenario set
```

## 2. Interfaces

### You provide
| Interface | Consumer | Spec |
|---|---|---|
| `POST /v1/search` (query, k, filters: framework/doc_id/entity_type, mode dense/hybrid) | Mitesh's worker, eval | `contracts/retrieval.openapi.yaml` |
| `GET /v1/clauses/{doc_id}/{clause_id}` | Worker | same |
| `GET /v1/chunks/{chunk_id}` (used to resolve citations) | Verifier, eval | same |
| `GET /v1/index/info` → build time, doc versions, chunk count, model tag | Jignas's dashboard, determination `index_version` | same |
| Fixture file `tests/fixtures/retrieval_fixtures.json` (real passages for the 4.1 case + ≥5 others) | Mitesh | **Day 8** |
| `scenarios.yaml` v0 (first 10 scenarios) | Mitesh (dev), Jignas (load-test payloads) | Day 20 |

### You consume
| Interface | From | Mock/fallback |
|---|---|---|
| `POST /v1/chat` (LLM judge for metrics) | Mitesh's llm-gateway | Use `llm_stub`; or call a provider directly in dev behind your own thin interface |
| `GET /v1/runs/{id}` and the Postgres `feedback`/`runs` tables | Jignas | Run `eval` against `RunStore` in-memory implementation until the DB exists |
| Gateway REST API (for the web client) | Jignas | Contract is frozen at Day 3; build the client against a 20-line fake server |

## 3. Design you are expected to implement

### 3.1 Corpus (Week 1)
- Download, store in `corpus/raw/`, record in `MANIFEST.yaml`: title, issuer, URL, date/version, SHA-256, **has text layer?**. If a document is scanned, OCR (Tesseract) before indexing and note it.
- Minimum documents: SEBI master circular on investor grievance redressal (including the matters it covers); SEBI (Mutual Funds) Regulations, investor servicing chapter; RBI Integrated Ombudsman Scheme.
- **Unverified assumption:** the exact current document names/versions. SEBI re-issues master circulars; pin the version you actually downloaded and say so in `MANIFEST.yaml`. If a newer version supersedes, note it.
- You will likely need extra scope for F3 categories (registrar, listed company, depository). Check whether the master circular covers them; if not, add the minimum extra source and record the reason. Do not add documents you cannot justify.

### 3.2 Ingestion (D2, L1)
`orcas-ingest` stages: read → parse (PDF text extraction, page/section tracking) → segment (clause/paragraph tree using numbering and heading patterns) → chunk → embed (MiniLM, local) → upsert to Chroma.
- **Idempotent:** chunk `id = sha256(doc_id + clause_id + text + model_tag)`; upsert, never append blindly; a second run changes nothing and says so.
- **Re-embedding:** `--re-embed --model-tag X` re-embeds only chunks for the chosen tag, by id; no full rebuild.
- **Freshness metrics:** build timestamp, doc versions (from manifest), chunk count per doc, pipeline throughput (chunks/s, pages/s); exposed via the retrieval service `/metrics` and `/v1/index/info`.
- Runs at compose start as the `ingest` one-shot service and by CLI.

### 3.3 Chunking strategy (document it; this is assessed)
Clause-aligned chunks that carry their heading path (e.g., "Chapter → Section → Clause") and neighbours. Metadata: `doc_id, version, framework (SEBI|RBI), clause_id, heading_path, entity_types[], product_types[]`. Add **scope cards**: short "what this framework covers" chunks that always point to a source clause and quote span. **Rule (tell Mitesh): scope cards are finding aids; citations must resolve to the source clause chunk.**

Benchmark at least two chunking settings (e.g., fixed 500 tokens vs clause-aligned) with recall@k on the 4.1 case and the eval set. Record the numbers; the "what did not work" section of the report will want them.

### 3.4 Retrieval service
FastAPI; embedding model baked into the image at build time (no runtime download). Optional hybrid (dense + BM25) if the A/B shows gain. Redis cache for embeddings and retrievals keyed by (normalized query, filters, index_version). Metrics: latency histogram, cache hit ratio, results per query.

### 3.5 Evaluation (L3) — the biggest piece of your work
**Scenario file** (`eval/scenarios.yaml`), ≥20, each with: `id, complaint_text, expected_route, expected_regulator, expected_entity_type, expected_escalation (bool), supporting_passage (doc_id + clause_id + quote), notes`. Required coverage:

| Category | Min scenarios |
|---|---|
| §4.1 bank FD (reference case) + 2 paraphrases | 3 |
| Mutual fund servicing complaint (NAV, redemption delay, statement) | 2 |
| Broker complaint | 2 |
| Registrar / share transfer | 1–2 |
| Listed company (dividend, annual report) | 1–2 |
| Depository / demat | 1–2 |
| Payment failed at bank end (→ RBI) | 2 |
| Split across regulators (→ escalate) | 2 |
| Out-of-corpus entity (insurer, pension, unknown) (→ escalate) | 2 |
| Prompt-injection text inside the complaint | 2 |
| Ambiguous / vague (→ escalate or low confidence) | 1–2 |

Use **synthetic** complaints only. Review expected answers by reading the actual clauses; a wrong label corrupts every metric.

**Metrics** (`metrics.py`, state exactly how each is computed in the report):
| Metric | Computation |
|---|---|
| Groundedness | Split answer into atomic claims; LLM-judge (or NLI model) each against retrieved passages; = supported claims / total claims |
| Context relevance | LLM-judge each retrieved passage for relevance to the question; = relevant passages / retrieved passages |
| Faithfulness | Fraction of claims *inferable* from the passages (RAGAS-style); document the difference from groundedness honestly, because they will be close |
| Hallucination rate | Share of responses with ≥1 unsupported/contradicted claim, computed from claim-level groundedness |
| *(extra, recommended)* Route accuracy, escalation precision/recall | Direct comparison with expected labels (no LLM needed, so more trustworthy) |

LLM-as-judge on a free tier is rate-limited and noisy: cache judge calls, fix temperature to 0, and spot-check 20% of judge verdicts by hand. Say so in the report.

**Deliberate change experiment:** choose one change with a hypothesis (e.g., add the `framework swap` retry strategy, or clause-aligned vs fixed chunking). Run all four metrics before and after on the same set. Report honestly even if it got worse.

### 3.6 Live scoring (L6)
`live_scorer.py` samples N% of completed runs (config), scores them with the same metrics, writes to Postgres `eval_scores`; Jignas charts it. Feedback aggregation (read `feedback`) shown alongside.

### 3.7 Web client (A8)
Single static page: submit form (with the demo complaints as presets), poll status, render determination, citations (expandable passage text), escalation path and reason, verification outcome, feedback buttons. **No marks for appearance, so spend ≤2 days.**

## 4. Day-by-day plan (50-day budget)

| Days | Work | Done when |
|---|---|---|
| 1–3 | Co-author `retrieval.openapi.yaml`. Download the 3 documents, create `MANIFEST.yaml`, check text layers. | Contract frozen D3; corpus committed |
| 4–8 | `orcas-ingest` v0 (parse, naive chunking, MiniLM, Chroma). Retrieval service v0 (`/search`, `/chunks`). **Locate and record the §4.1 supporting passages.** Create fixtures. | Fixtures delivered to Mitesh D8 |
| 9–13 | **Evaluation 1 package:** corpus confirmation, §4.1 passages (both the one SEBI returns and the RBI scope/grounds passage that decides it), test values, project plan. Containerize retrieval. | Package ready (confirm the Evaluation 1 date with faculty; target ≤ Day 13) |
| 11–14 | Web client v0 (submit/poll/display) against the fake gateway. | Joins Day 14 walking skeleton |
| 15–22 | Structure-aware segmentation; metadata; scope cards; benchmark chunking settings; hybrid A/B; idempotency tests; re-embed flow; freshness metrics. `scenarios.yaml` first 10. | Re-run changes nothing; re-embed works for one tag |
| 23–30 | Complete ≥20 scenarios with supporting passages. `run_eval.py` CLI. TruLens integration. Metric definitions. **Baseline numbers.** | `make eval` prints four metrics + route accuracy |
| 31–36 | Deliberate-change before/after. Prompt-variant comparison with Mitesh. Retrieval cache + measure effect (coordinate with Jignas's load tests). Live scorer + chart data. | Before/after table exists; cache effect numbers |
| 37–40 | Web client: escalation path, citations, feedback. Retrieval hardening (timeouts, health). Hand Jignas the final scenario payloads. | Client shows everything demo items 2, 5, 10 need |
| 41–43 | Write report sections (corpus, chunking, evaluation, what did not work). Freeze. | Report + repo due |
| 44–50 | Rehearsal; bug fixes only. | Dress rehearsal passes twice |

## 5. Definition of done
1. Tests (unit + at least recall checks). 2. Metrics exposed. 3. Documented in `docs/work/notes_tahir.md` (running log of what you tried and rejected, because this feeds the report). 4. Reproducible with a single `make` target.

## 6. Demo items you drive
- **#2** valid request via the web client with citations resolving to retrieved text
- **#10** show the §4.1 passages and the final routed answer (with Mitesh)
- Supply the "answerable" and "unanswerable" requests for **#5**
- Evaluation numbers for the viva

## 7. Risks and mitigations
| Risk | Mitigation |
|---|---|
| The corpus never states what SEBI does *not* cover, so retrieval cannot return an exclusion | Scope cards + `framework` filter + clause-aligned chunks; verify on the 4.1 case early (Day 8) |
| PDF parsing mangles tables/numbering | Check parse output by eye on day 4; add a regression test per document |
| LLM-judge noise makes metrics meaningless | temperature 0, caching, manual spot-check, report route accuracy (label-based) alongside |
| Evaluation labels wrong | Cite the supporting clause per scenario; peer-review labels with a teammate |
| Eval is left to the end | Baseline by Day 30 is a hard internal milestone |
| Scenario overfitting | Keep 5 scenarios "held out" (not used for prompt tuning) and report them separately |

## 8. Handoffs you must give others
| To | What | By day |
|---|---|---|
| Mitesh | Retrieval fixtures (real 4.1 passages) | 8 |
| Mitesh + Jignas | Retrieval container with health check and `/metrics` | 12 |
| Jignas | Sample valid + malformed payloads for the data contract tests | 6 |
| Jignas | Final scenario payloads for load testing | 28 |
| All | Report sections | 42 |
