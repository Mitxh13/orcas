# Evaluation (L3) — design, metrics, results

How ORCAS's quality is measured, the exact metric computations, and the current results.
Run: `make eval` (the agent, stub replies → `eval/results/agent-stub.json`), `make eval-live`
(the agent, live model → `agent-gateway.json`), `make eval-baseline` (keyword reference →
`baseline.json`).

## 1. Scenarios
`eval/scenarios.yaml` — **25 synthetic scenarios** (coverage below), each with `expected_route`,
`expected_regulator`, `expected_entity_type`, `expected_escalation`, and a `supporting_passage`
(doc_id + clause_id + exact quote) where a provision applies (15 of 25). **7 are `held_out: true`**
and excluded from any tuning — including the LLM stub's keyword rules — and reported separately.
The 5 injection scenarios also carry `expected_intake: rejected`: the gateway should quarantine them
before any run. In-process evaluation runs them straight through the agent as well, to test the
agent's own defence.

| Category | n | Category | n |
|---|---|---|---|
| §4.1 bank FD (+2 paraphrases) | 3 | RBI payment/ATM | 2 |
| Mutual fund servicing | 2 | Split regulators → escalate | 2 |
| Broker | 2 | Out-of-corpus (insurer/pension) → escalate | 2 |
| Registrar / share transfer | 2 | Prompt injection → reject at intake / escalate | 5 |
| Listed company | 2 | Ambiguous / vague → escalate | 1 |
| Depository / demat | 2 | | |

Labels are pinned from the actual clauses (not memory); every pinned supporting clause resolves to
an indexed chunk containing its quote. The two mutual-fund scenarios were re-pinned on 2026-11-03 to
Regulation 60A (the AMC must redress investor grievances within twenty-one calendar days; indexed
under clause `60-2`) — the old pin was an amendment footnote about an omitted provision. Labels
still need **peer review by a teammate** before the final run: `docs/work/tahir/scenario-review.md`
lists every label with its passage and a sign-off box.

## 2. System under test (SUT)
**Default: the shipped agent** (`eval/agent_sut.py`). It runs the worker's
`run_complaint_pipeline` (Researcher → Verifier → output filter) with the real retrieval app:
- **stub** (`make eval`): LLM replies from the `llm_stub` app, exactly what `llm-gateway` returns in
  `ORCAS_STUB_MODE`. Needs only the local index. It measures the pipeline with canned replies, **not
  model quality**.
- **live** (`make eval-live`): LLM calls go to `LLM_GATEWAY_URL`.
- **via gateway** (`--via-gateway URL`): a running stack through its public API.

**Reference only: the keyword baseline** (`eval/sut.py`), a transparent rule classifier plus
framework-filtered retrieval. Its label metrics are perfect by construction (it mirrors the label
scheme), so it is not evidence of system quality.

## 3. Judge
A **deterministic embedding (NLI-substitute) judge** (`eval/judge.py`): claim support / passage
relevance / inferability are decided by cosine similarity of local MiniLM embeddings against
explicit thresholds — **support 0.45, relevance 0.32, infer 0.40** (also written into every results
file as `judge_thresholds`). It is reproducible, free and not rate-limited. No LLM judge is used.

Its known weakness: a short, generic sentence ("…should be routed under the cited RBI provision")
can clear the support threshold against any on-topic passage. Groundedness is therefore read
together with the label-based supporting-passage metrics below, which need no judge.

## 4. Metric definitions (exact)
**Claims** are the sentences of the answer's own text — for the agent, the `basis` of a completed
determination (citation tags removed); for the baseline, its routing conclusion. Sentences copied
from the retrieved passages are *not* claims: they are supported by construction.
**Contexts** are the passages the run retrieved (for the agent, its `tool_call` events), not the
ones it chose to cite.

RAG metrics are computed only over **answered** scenarios (escalations assert nothing):
- **Groundedness** = supported_claims / total_claims, claim-level (`judge.supports`), macro-averaged.
- **Context relevance** = relevant_passages / retrieved_passages (`judge.relevant`), macro-averaged.
- **Faithfulness** = inferable_claims / total_claims (`judge.inferable`, a *looser* bar than support,
  RAGAS-style). It is **highly correlated with groundedness by construction** — reported honestly.
- **Hallucination rate** = share of answered scenarios with ≥1 unsupported claim.

Label metrics are computed over **all** scenarios (no judge → more trustworthy):
- **Route / regulator / entity accuracy** = exact match vs expected.
- **Escalation precision / recall** = escalation as the positive class.

Intake metrics (the gateway's job, independent of the SUT; no judge):
- **Intake rejection recall** = share of the `expected_intake: rejected` scenarios that the gateway
  guardrail rejects (in-process runs ask the guardrail directly; via-gateway runs use the response).
- **Intake false rejections** = number of other scenarios the guardrail rejects (should be 0).

Supporting-passage metrics are computed over the 15 scenarios that pin a passage (no judge):
- **Supporting passage retrieved** = share where a retrieved chunk is the pinned clause's chunk.
- **Supporting passage cited** = share where a cited chunk is the pinned clause's chunk (not
  applicable to the baseline, which does not cite).

## 5. Results (2026-11-12)

Re-recorded after the November agent changes (stub and agent rewrite, verifier retry, intake
guardrail patterns) on index `minilm-l6-v2.clause.637.dd539860ac65`, prompts classify v3, reflect v2,
draft v1, verifier v1. The 2026-11-03 figures described a pipeline that no longer ships.

### 5.1 The agent, stub mode — `eval/results/agent-stub.json`

| Metric | All (25) | Held-out (7) |
|---|---|---|
| answered (non-escalated) | 18 | 4 |
| groundedness | 1.000 | 1.000 |
| context_relevance | 0.561 | 0.450 |
| faithfulness | 1.000 | 1.000 |
| hallucination_rate | 0.000 | 0.000 |
| route_accuracy | 0.720 | 0.429 |
| regulator_accuracy | 0.720 | 0.429 |
| entity_accuracy | 0.680 | 0.429 |
| escalation_precision / recall | 0.714 / 0.500 | 0.333 / 0.333 |
| supporting passage retrieved | 0.133 | 0.000 |
| supporting passage cited | 0.000 | 0.000 |
| intake rejection recall | 1.000 | 1.000 |
| intake false rejections | 0 | 0 |

Reading: 18 runs completed and 7 escalated (5 `out_of_corpus`, 2 `split_regulators`); none escalated
as `unverified`, against 8 on 2026-11-03. The 1.0 groundedness still covers single generic sentences
that the embedding judge accepts, so it is not a quality signal. The supporting-passage metrics show
what the judge misses: the agent retrieves the pinned passage in 2 of 15 scenarios and cites it in
none. For §4.1 the agent now routes to RBI and cites the scheme's preamble, one of the two deciding
passages; the scenario pins clause 9, which the agent's `k=5` search still does not return, so the run
counts as a miss on this metric. The stub draft cites one passage chosen by keyword, which is why
this metric cannot improve in stub mode. The gateway guardrail rejects all 5 injection scenarios
(2 of 5 on 2026-11-03). The 7 route mispredictions are those 5 injections, whose label expects a
rejection and not a route, plus `ref_4_1_paraphrase_1` and `broker_nonpayment`.

Held-out numbers rest on 7 scenarios: one scenario moves a rate by 0.14.

**Live-model results:** none yet. `LLM_GATEWAY_URL=http://localhost:8002 make eval-live` produces
them once the stack runs with `ORCAS_STUB_MODE=false`.

### 5.2 Keyword baseline (reference only) — `eval/results/baseline.json`

| Metric | All (25) | Held-out (7) |
|---|---|---|
| groundedness | 0.722 | 0.800 |
| context_relevance | 0.900 | 0.920 |
| faithfulness | 0.833 | 0.800 |
| hallucination_rate | 0.278 | 0.200 |
| route / regulator / entity accuracy | 0.880 | 0.857 |
| escalation_precision / recall | 1.000 / 0.700 | 1.000 / 0.667 |
| supporting passage retrieved | 0.333 | 0.250 |

The baseline mirrors the label scheme, so it is right everywhere except the three paraphrased
injections, which its keyword list does not recognise.

### 5.3 Superseded: the Day-30 baseline
The Day-30 table (groundedness 0.911, context relevance 0.907, faithfulness 0.933, hallucination
0.267) **is withdrawn**. It scored the keyword baseline, not the agent, and two of its three claims
per answer were sentences copied from the retrieved passages, which are supported by construction.
With claims restricted to the answer's own text, the same baseline scored groundedness 0.733 and
faithfulness 0.800 on that 22-scenario set. Recorded in `notes_tahir.md` under "what didn't work".

## 6. Honesty notes (for the report)
- Stub-mode agent numbers measure the pipeline with canned replies, not model quality.
- The baseline's perfect label metrics are by construction; they are not an independent measurement.
- Groundedness and faithfulness track each other closely (same embedding judge, nearby thresholds),
  and the judge accepts generic sentences — read them with the supporting-passage metrics.
- **25 scenarios give wide confidence intervals** — treat deltas smaller than a few points as noise.
- TruLens: `python -m eval.trulens_eval` scores the same agent runs through TruLens primitives (a
  custom embedding feedback **Provider**, `Feedback` objects, a persisted `TruSession`). Its feedback
  values are raw cosine similarities (stub mode, 2026-11-12: groundedness 0.561, context relevance
  0.347 over the answered runs), not the thresholded claim decisions, so they are not comparable with the table above.
  The claim-level metrics in `metrics.py` remain primary.

## 7. Experiments, cache, live scoring (Slice 6)

### 7.1 Deliberate change (L3) and prompt comparison (L2): pre-registered, on the agent
The old framework-filter before/after and dense-vs-hybrid comparison ran on the keyword baseline with
copied-passage claims; they are withdrawn and their results file removed. Hybrid retrieval was also
re-checked against the pinned supporting passages (complaint text as query, top 5): dense and hybrid
both find 5 of 15, so it is not a useful deliberate change.

Both experiments are now **pre-registered** in `eval/experiments.yaml`: change, hypothesis and primary
metric were committed before any result exists, and git history shows the order.

| Experiment | Requirement | Change | Primary metric |
|---|---|---|---|
| `l2-reflect-prompt` | L2 | reflect prompt v1 vs v2 (modifier and exclusion hunt) | route accuracy |
| `l3-cite-by-evidence-number` | L3 | draft prompt v1 (copy chunk ids) vs v2 (cite numbered evidence) | supporting passage cited |

`python -m eval.experiment run <name> --llm gateway` evaluates both configurations on the same 25
scenarios and model, each in a fresh process with its environment overrides, and writes
`eval/results/exp-<name>.json` with all four RAG metrics, the label metrics, supporting-passage hits,
LLM call counts and a verdict on the primary metric (deltas of one or two scenarios are flagged as
within noise). `compare <name> before.json after.json` does the same for two existing results files.

**Status:** both need only a live model now (stub replies do not depend on prompt wording; the
harness refuses stub mode unless `--allow-stub`). The prompt-version switch is merged, and draft v2
is wired: the model cites numbered evidence and the worker fills in the chunk. In stub mode, draft
v1 and v2 give identical outcomes on all 25 scenarios, which confirms the plumbing and nothing
about model quality.

**Budget:** one configuration is 25 scenarios × roughly 4–11 LLM calls, so one experiment is about
200–550 calls. Check the hosted free tier's daily quota first; if it does not fit, run against an
Ollama-backed gateway and say so in the report.

### 7.3 Retrieval cache effect (D7) (`python -m eval.cache_effect`)
Key = `sha256(normalised_query | filters | mode | k | index_version)`; Redis when `REDIS_URL` set,
else in-process TTL dict.

| | hit ratio | p50 | p95 |
|---|---|---|---|
| cold | 0.00 | 17.76 ms | 41.96 ms |
| warm | 1.00 | 0.09 ms | 0.12 ms |

Warm p95 ≈ **0.1 ms** vs cold ≈ 42 ms. The `index_version` in the key means a re-index transparently
invalidates stale entries. (Coordinate the production Redis numbers with Jignas's load test.)

### 7.4 Live scoring (L6) — `python -m eval.live_scorer`
Scores a deterministic sample of **real completed runs** from Postgres with the same evidence rules
and judge as the evaluation (`eval/run_evidence.py`: claims = the basis sentences, contexts = the
passages the run retrieved, relevance judged against the complaint text). It writes one
`eval_scores` row per metric — groundedness, faithfulness, context relevance and a per-run
hallucination flag (whose average is the hallucination rate) — tagged `source=live_scorer` with the
judge and thresholds. The worker-metrics exporter publishes `orcas_eval_score{metric=...}`, which
Grafana charts over time; feedback totals come from the `feedback` table.

- Sample rate `LIVE_SCORE_SAMPLE_RATE` (default 0.2), stable hash of the run id.
- Already-scored runs are skipped, so passes are idempotent; escalations assert nothing and are not
  candidates; runs with no retrieved passages (e.g. served from the run cache) are skipped.
- `--loop 60` keeps scoring (the Compose `live-scorer` service runs this from the retrieval image).

Checked end to end against PostgreSQL 17: three completed agent runs were scored (one run without
retrieved passages skipped), a second pass scored nothing, and the exporter published the scores.
The earlier in-memory demo, with synthetic feedback, is removed.

## 8. Next
T3: run the two pre-registered experiments against a live model, then record `make eval-live`.
