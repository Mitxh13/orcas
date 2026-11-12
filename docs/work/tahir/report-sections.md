# Report sections — Data, Retrieval & Quality (Tahir)

Consolidated, freeze-ready report sections for Tahir's part of ORCAS. Detailed sources:
[`chunking.md`](chunking.md), [`evaluation.md`](evaluation.md),
[`evaluation-1-package.md`](evaluation-1-package.md), [`corpus/MANIFEST.yaml`](../../../corpus/MANIFEST.yaml),
running log [`notes_tahir.md`](notes_tahir.md).

---

## A. Corpus

Three regulatory documents, downloaded and pinned in `corpus/MANIFEST.yaml` with real SHA-256 and a
text-layer flag (all have a text layer; no OCR needed).

| doc_id | Title | Issuer | Version pinned | Pages |
|---|---|---|---|---|
| `sebi_mutual_funds_regulations_1996` | SEBI (Mutual Funds) Regulations, 1996 | SEBI | consolidated to Jan 2026 | 162 |
| `rbi_integrated_ombudsman_scheme_2021` | Reserve Bank – Integrated Ombudsman Scheme, 2021 | RBI | base 2021 (12-Nov-2021) | 18 |
| `sebi_scores_master_circular` | Master Circular on redressal of investor grievances through SCORES | SEBI | `…/CIR/2022/0150`, 07-Nov-2022 | 11 |

- **Circular-level citations:** every chunk carries its document's reference from the manifest, copied from
  the document itself: the SCORES master circular number (`SEBI/HO/OIAE/IGRD/P/CIR/2022/0150`), the MF
  Regulations' Gazette notification (`S.O. 856 (E)`, 9 December 1996), and the RBI scheme's statutory basis
  (the scheme PDF prints no notification number, so none is invented).
- **F3 coverage confirmed:** the SCORES master circular covers listed companies, brokers, depository
  participants, mutual funds/AMCs, registrars/RTAs and depositories — one SEBI document covers the
  registrar/listed-company/depository categories; no extra source needed.
- **Supersession (honest):** the 2022 SCORES master circular was later superseded by SCORES 2.0
  (circular `2023/156`, 20-Sep-2023), which changed operational *timelines*. We index the 2022 master
  circular (clean, self-contained, sufficient for §4.1 jurisdiction); timeline-sensitive eval labels
  are checked against 2023/156. Recorded in the manifest's `superseded_by`.
- The §4.1 deciding provisions are pinned: SEBI SCORES clause 2 (apparent) and RBI **preamble** (scope:
  "entities regulated by Reserve Bank") + RBI clause **9** (Grounds of Complaint, "deficiency in service").

## B. Chunking & retrieval (L1)

Clause-aligned segmentation is the production default: regex clause/`CHAPTER` detection → chunks with a
real `clause_id` + `heading_path`, preamble captured, long clauses split into `#k` sub-chunks, each
tagged with inferred `entity_types`/`product_types`. Scope cards (one per framework) are finding aids
that carry `source_clause_chunk_id` — **citations resolve to the source clause, never the card.**
Retrieval is orchestrated with LangChain: a `BaseRetriever` over Chroma with a LangChain `Embeddings`
wrapper for the local MiniLM model. Dense (cosine) search with `framework`/`doc_id`/`chunk_type` filters pushed to Chroma and
`entity_type`/`product_type` post-filters; optional hybrid (dense+BM25, RRF); Redis-or-in-memory cache
keyed by `(norm query, filters, mode, k, index_version)`.

**Benchmark (`python -m eval.benchmark_chunking`), 6 topical framework-filtered probes:**

| strategy / mode | recall@5 | recall@10 | mean_hit_score |
|---|---|---|---|
| fixed / dense | 1.00 | 1.00 | 0.650 |
| clause / dense | 0.833 | 0.833 | **0.719** |
| clause / hybrid | 0.833 | 0.833 | n/a |

Decision: ship **clause-aligned** — it trades a marginal raw-recall dip (fixed-window chunks are bigger,
so they trivially contain more phrases) for sharper ranking (0.719 vs 0.650; the RBI grounds provision
retrieves at 0.809 vs ~0.63 naive) and **citable clause-level provisions**, which is what verification
needs. Hybrid showed no recall gain → off by default, available via `mode`.

## C. Evaluation (L3)

- **25 synthetic scenarios** (`eval/scenarios.yaml`), full category coverage, **7 held out** from tuning;
  15 pin a supporting passage (doc + clause + exact quote), all of which resolve in the index; the 5
  prompt-injection scenarios are labelled for rejection at intake.
- **System under test:** the shipped agent (`eval/agent_sut.py` runs the worker pipeline with the real
  retrieval app). The keyword baseline (`eval/sut.py`) is kept as a reference only.
- **Judge:** deterministic embedding (NLI-substitute) judge; thresholds support 0.45, relevance 0.32,
  infer 0.40. No LLM judge is used.
- **Metrics (exact computations in `evaluation.md`):** groundedness, context relevance, faithfulness,
  hallucination rate (claim-level, claims = the answer's own sentences); route/regulator/entity accuracy
  and escalation precision/recall; supporting passage retrieved / cited and intake rejection (label-based,
  no judge).

**The agent, stub mode (`make eval`, 2026-11-12):**

| metric | all (25) | held-out (7) |
|---|---|---|
| answered | 18 | 4 |
| groundedness / faithfulness | 1.000 / 1.000 | 1.000 / 1.000 |
| context_relevance | 0.561 | 0.450 |
| hallucination_rate | 0.000 | 0.000 |
| route / regulator / entity acc | 0.720 / 0.720 / 0.680 | 0.429 / 0.429 / 0.429 |
| escalation precision / recall | 0.714 / 0.500 | 0.333 / 0.333 |
| supporting passage retrieved / cited | 0.133 / 0.000 | 0.000 / 0.000 |
| intake rejection recall / false rejections | 1.000 / 0 | 1.000 / 0 |

Stub replies are canned, so these numbers describe the pipeline, not model quality. The judge scores the
answers' single generic sentence as grounded; the label-based metrics show that the pinned supporting
passage is retrieved in 2 of 15 scenarios and cited in none. For §4.1 the agent routes to RBI and cites the
scheme's preamble; the scenario pins clause 9, which the agent's `k=5` search does not return. The gateway
guardrail rejects all 5 injections. No live-model results exist yet.

**Keyword baseline (reference only, `make eval-baseline`):** groundedness 0.722, context relevance 0.900,
faithfulness 0.833, hallucination 0.278, supporting passage retrieved 0.333; label metrics 0.880 (it mirrors
the label scheme but misses the three paraphrased injections).

- **Deliberate change (L3) and prompt comparison (L2):** the earlier framework-filter and dense-vs-hybrid
  comparisons ran on the keyword baseline, so they are withdrawn. Two agent experiments are pre-registered
  in `eval/experiments.yaml` (hypothesis and primary metric committed before running): reflect prompt v1 vs
  v2 (L2, route accuracy) and citing by evidence number vs copying chunk ids (L3, supporting passage cited).
  They run with `python -m eval.experiment run <name>` once a live model and the prompt-version switch exist.
- **Retrieval cache (D7):** warm p95 ≈ 0.1 ms vs cold ≈ 42 ms; `index_version` in the key invalidates
  stale entries on re-index.
- **Live scoring (L6):** `eval/live_scorer.py` scores a deterministic sample (default 20%) of real completed
  runs from Postgres with the evaluation's judge and evidence rules, writes groundedness, faithfulness,
  context relevance and hallucination to `eval_scores`, and Grafana charts them over time via the
  worker-metrics exporter; feedback is persisted and totalled alongside.

## D. What did not work (honest)

- **Automated corpus download hit walls:** Firecrawl ran out of credits; the RBI document host WAF-blocks
  `curl` (every variant returned a JS challenge). RBI + the SEBI master circular were fetched via browser;
  provenance recorded in the manifest.
- **Fixed-window chunking "won" raw recall@k** — an artefact of larger chunks, not better retrieval. We did
  not chase that number; clause-aligned is the right choice for grounded citations.
- **Hybrid retrieval gave no measurable recall gain** on this corpus, so it is off by default (not deleted
  — it stays available and is re-tested in Slice 5's set).
- **The first evaluation never scored the agent.** `make eval` ran only the keyword baseline, whose label
  metrics are 1.0 by construction, and two of its three claims per answer were sentences copied from the
  retrieved passages, so groundedness (0.911) was inflated. Found in the integration audit; the harness now
  scores the agent and claims come from the answer's own text (the baseline drops to 0.733).
- **The live-scoring demo used synthetic feedback** (an up-rate of 0.727 came from a fixed pattern, not
  users); those numbers are withdrawn.
- **No free LLM judge was wired** (no API key); the embedding/NLI judge stands in. Groundedness and
  faithfulness are consequently highly correlated. 25 scenarios give wide confidence intervals.
- **The mutual-fund supporting passage was a bad label** — it pinned an amendment footnote about an
  omitted provision. Re-pinned to Regulation 60A (AMC must redress grievances within 21 days), which the
  segmenter had folded into the chunk indexed as clause `60-2`.
- **Segmentation is a regex heuristic**, not a legal parser — dense index pages produce some noisy clause
  titles; documented, not hidden.
- **The embedding model's name is still in the repository** (Dockerfiles bake it at build time; code
  defaults name it). Strict D4 would pass it as a build argument from `.env`, but that makes building the
  images depend on `.env` and on a coordinated Compose change in the final week, a risk to the "starts from
  a clean clone" gate. It is a free, local model that the brief itself suggests; the LLM provider's model
  names are configuration only.
- **TruLens is a secondary view.** It scores the agent's runs with the same embedding judge through TruLens
  feedback primitives; its raw cosine scores are not comparable with the thresholded claim-level metrics,
  which stay primary.
- **The retrieval and ingest images are built and exercised only in CI** (the Compose smoke job builds them
  and runs the reference case end to end); they have not been built on a team laptop.

## E. Definition of done (verified at freeze)

| DoD item | Status |
|---|---|
| Unit tests + recall checks | ✅ `make test` → 204 passed, 2 skipped (2026-11-12, index built) |
| Metrics exposed | ✅ retrieval `/metrics` (`orcas_*`); `make eval` scores the agent and prints the four metrics, label metrics and supporting-passage hits |
| Running log kept | ✅ `notes_tahir.md` (per-slice) |
| Reproducible with a single `make` target | ✅ `make ingest` / `make eval` / `make test` |

**Freeze:** Slices 0–7 shipped (PRs #2–#9 merged). The three handoffs listed at freeze are done: the live
scorer runs as the Compose `live-scorer` service, the agent is the evaluation SUT, and CI builds the
retrieval image. Still open: a live-model evaluation run and the two pre-registered experiments.
