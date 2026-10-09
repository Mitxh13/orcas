# MITESH: Agent Core & LLM Layer

**Role in one line:** you build the brain: the two LangGraph agents, the verification gate, the LLM gateway with resilience, prompts, output guardrails and cost accounting.

You are on the critical path for the hardest requirements (F5, F7, A1, A2, A3, A5). Your work gets judged mostly on *whether the system knows when not to answer*.

---

## 1. What you own

### Requirements
| ID | Requirement | Your piece |
|---|---|---|
| F3, F5, F6, F7 | Classification, grounded determination, routing, escalation | Agent logic |
| A1 | LangGraph reasoning layer, ≥3 tools, bounded loops | Worker graph + tools |
| A2 | ≥2 agents, hand-off, documented failure behaviour | Researcher + Verifier |
| A3 | Verify every answer; citations resolve; no unverified output | Verifier + citation resolver |
| A5 | Timeouts, bounded retry w/ backoff, circuit breaker, fallback path | `llm_gateway` + tool wrappers |
| L2 | Prompts as versioned artifacts; compare ≥2 variants | `prompts/` + prompt loader |
| L4 (agent side) | Prompt isolation, tool-boundary sanitisation, **output filtering**, output toxicity | Worker |
| L5 | Tokens + cost per call, per step, per run | `llm_gateway` accounting |
| D6 (partial) | The LLM stub | `services/llm_stub/` |
| A6 (partial) | Emit per-agent metrics (steps, tool calls, retries, per-step latency) | Worker metrics |

### Directories (only you commit here without review from the others)
```
services/worker/            # LangGraph agents (Researcher, Verifier), tools, state, task entrypoint
services/llm_gateway/       # provider adapters, breaker, backoff, fallback, cost accounting
services/llm_stub/          # fixed responses with configurable delay
prompts/                    # versioned prompt YAML: id, version, text, variables, changelog
tests/agent/                # agent unit tests with mocked retrieval + mocked LLM
```

## 2. Interfaces

### You provide
| Interface | Consumer | Spec |
|---|---|---|
| Celery task `orcas.run_complaint(run_id)` | Jignas's gateway enqueues it | `contracts/README.md` |
| `POST /v1/chat` on llm-gateway | Your worker, Tahir's eval harness (LLM judge) | `contracts/llm_gateway.openapi.yaml` |
| Determination JSON written via `RunStore.complete(run_id, determination)` | Jignas (API returns it), Tahir (web/eval read it) | `contracts/determination.v1.schema.json` |
| Run events via `RunStore.append_event(...)` | Jignas's trace endpoint | Event kinds in `contracts/README.md` |
| Prometheus metrics on `/metrics` | Jignas's dashboards | Metric names in `docs/ARCHITECTURE.md §8` |

### You consume
| Interface | From | How to work before it exists |
|---|---|---|
| `POST /v1/search`, `GET /v1/clauses/...`, `GET /v1/chunks/{id}` | Tahir's retrieval | **Mock first**: `tests/fixtures/retrieval_fixtures.json` + a `FakeRetrievalClient`. Tahir delivers the 4.1 fixture passages by **Day 8**; until then write placeholder passages yourself. |
| `RunStore` | Jignas's `orcas_common` | In-memory implementation delivered by Day 5; until then use a dict-backed one. |
| Logging / correlation / OTel helpers | Jignas's `orcas_common` | Day 5. |

> **Independence rule:** every behaviour you build must be runnable with `FakeRetrievalClient` + `llm_stub` and zero other services. If you ever have to wait on a teammate, escalate in the daily sync; do not block silently.

## 3. Design you are expected to implement

### 3.1 Researcher agent (LangGraph)
State: `complaint_fields, classification, evidence_ledger, queries_tried, strategy_idx, budget{steps, tool_calls, started_at}, draft`.
Nodes: `normalize → classify → research_loop(plan → tool → reflect) → draft`.
Tools (≥3, chosen by the model, not fixed stages):
1. `search_corpus(query, framework?, entity_type?, k)` → passages
2. `get_clause(doc_id, clause_id)` → clause text
3. `get_complaint_fields(fields[])` → the request's own fields
4. `list_framework_scope(framework)` → scope-card entry points (finding aid only)

The `reflect` node must explicitly ask: *"Does anything I read modify, exclude, or redirect this provision? What product and entity class did the complaint actually describe?"* and derive the next query from retrieved text. This is the behaviour the §4.1 case tests.

### 3.2 Verifier agent
Separate graph, separate prompt, separate tool permissions (read-only `resolve_citation`). It:
1. Splits the draft into atomic claims.
2. Checks each claim against the cited passages (claim-level groundedness).
3. Confirms every `chunk_id` in citations resolves via `GET /v1/chunks/{id}` and the `quote_span` occurs in the text.
4. Confirms the `route` is consistent with the evidence and in the allowed enum.
Returns `accept | retry(strategy) | escalate(reason_code)`.

### 3.3 Failure behaviour (document in the report)
See `docs/ARCHITECTURE.md §4.3`. Key rule: **an unverified or incomplete run is never returned as a confident answer.**

### 3.4 LLM gateway
- Provider adapters through LiteLLM; provider/model/key only from env.
- Timeouts on every call; bounded retries with exponential backoff + jitter; circuit breaker with shared state in Redis; fallback chain: secondary model → cache of identical prompt → honest error (`degraded=true`).
- Accounting: record prompt tokens, completion tokens, USD cost at call time, tagged with `run_id`, `agent`, `step`, `prompt_version`.
- Tool-calling on free-tier models can be unreliable. **Build a JSON action protocol fallback** (model emits `{"action": "...", "args": {...}}`) validated by Pydantic; do not depend on native function-calling only.

### 3.5 Prompts
`prompts/<name>/<version>.yaml`. Every response stores the prompt versions used. Make at least two variants of the Researcher *reflect* prompt (e.g., `v1` baseline vs `v2` explicit "modifier hunt"). Tahir's harness runs both on the eval set; you write the report paragraph with the winner, metric and delta.

### 3.6 Output guardrails (L4)
- Allowed `route` ∈ {`assign_to_entity`, `refer_to_other_regulator`, `not_maintainable`, `escalate_to_human`}; anything else is blocked.
- `disposed` must be `false`. Block phrases that imply final disposal or relief.
- Toxicity check on output (use a lightweight local classifier or rule list; document the choice).
- Untrusted complaint text is wrapped as quoted data; the system prompt states it is data, never instructions; tool arguments are validated and never taken verbatim from complaint text without checks.

## 4. Day-by-day plan (50-day budget)

| Days | Work | Done when |
|---|---|---|
| 1–3 | Co-author contracts (`determination`, `llm_gateway`, event kinds). Create `services/worker`, `llm_gateway`, `llm_stub` skeletons. Pick the LLM provider, set a **spending limit**. | Contracts frozen at the D3 sync |
| 4–9 | `llm_stub` v0. `llm_gateway` v0 (one provider, usage capture). Worker v0: single Researcher loop with `FakeRetrievalClient`, easy cases only. Celery task entrypoint. | `pytest tests/agent` passes; one fake run returns a schema-valid determination |
| 10–14 | **Walking skeleton integration:** real retrieval client, real Celery path, `RunStore` writes. | **Day 14: valid request → routed result through the real compose stack** (project-wide gate) |
| 15–22 | Verifier agent; citation resolution; escalation rules (F7); loop bounds (A1); hand-off + failure documentation. | Verifier rejects a planted unsupported claim |
| 23–28 | Multi-step research: modifier search, query-from-read-text, retry strategies. Prompt registry + versions (L2). Output filter and prompt isolation (L4). | **§4.1 case passes** on real retrieval with citations resolving |
| 29–34 | Resilience (A5): timeouts, backoff, Redis-shared breaker, fallback chain, `degraded` flag. Cost per step (L5). Per-agent metrics (A6). | Breaker opens on killed provider; degrade path shown |
| 35–40 | Prompt variant comparison with Tahir's harness. Failure-injection demos for items 5–8. Tuning loop limits using eval results. | Demo items 5, 6, 7(output side), 8 reproducible with one command each |
| 41–43 | Write your report sections (agent design, verification, loop limits, what didn't work). Freeze. | Report + repo due |
| 44–50 | Rehearsal, bug fixes only (no new features). | Dress rehearsal passes twice |

## 5. Definition of done (per feature)
1. Unit tests with mocked dependencies. 2. Metrics emitted. 3. Events written to run store with correlation ID. 4. Documented in the report notes (`docs/work/notes_mitesh.md`, running log). 5. Demo command in `scripts/demo/`.

## 6. Demo items you drive
- **#5** corpus cannot answer → escalation (e.g., an insurance complaint if the corpus has no IRDAI framework)
- **#6** self-verification catches an unsupported answer → re-search or escalate (provide a switch `ORCAS_INJECT_BAD_DRAFT=1` that plants a fabricated clause in the draft)
- **#7** output side of the injection demo
- **#8** breaker opens, degradation path (provide `scripts/demo/kill_provider.sh`)
- **#10** narrate the §4.1 case with Tahir showing the passages

## 7. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Free-tier rate limits throttle development | Develop against the stub; cache LLM responses by prompt hash in dev; small eval runs |
| Native tool-calling unreliable on small models | JSON action protocol fallback |
| Verifier is too lenient (rubber-stamps) or too strict (escalates everything) | Planted-bad-draft test set; track escalation rate vs. accuracy; tune on the eval set |
| Agent loops forever or burns budget | Hard bounds (steps, tool calls, wall clock) enforced outside the model |
| You become the bottleneck (largest scope) | Hand off L4 input-side to Jignas (already done); if behind at Day 30, cut order: fallback provider → prompt variant #3 → toxicity-on-output sophistication |

## 8. Handoffs you must give others
| To | What | By day |
|---|---|---|
| Jignas | Metric names list; list of run event kinds; stub service port/ENV | 5 |
| Tahir | List of queries the agent actually issues on the 4.1 case (to check retrieval quality) | 24 |
| Tahir | Prompt variants ready for comparison | 35 |
| All | Report sections | 42 |
