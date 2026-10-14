# ORCAS Frozen Data Contracts

This directory contains the authoritative interface specifications for the ORCAS system.
These contracts are **FROZEN** as of Day 3. Any modifications require an Architectural Decision Record (ADR) in `docs/decisions/` and unanimous approval from all three team members (Mitesh, Tahir, Jignas).

---

## 1. External & Service Interfaces

| Contract File | Purpose | Producer $\rightarrow$ Consumer |
|---|---|---|
| [`complaint.v1.schema.json`](complaint.v1.schema.json) | Request payload validated at ingestion boundary | Gateway $\rightarrow$ Client |
| [`determination.v1.schema.json`](determination.v1.schema.json) | Terminal complaint routing determination | Worker $\rightarrow$ Gateway / Eval |
| [`run_status.v1.schema.json`](run_status.v1.schema.json) | Polling response containing status and result | Gateway $\rightarrow$ Client |
| [`llm_gateway.openapi.yaml`](llm_gateway.openapi.yaml) | LLM chat completion API with usage & cost tracking | LLM Gateway $\rightarrow$ Worker |
| [`retrieval.openapi.yaml`](retrieval.openapi.yaml) | Search, chunk, and clause retrieval API | Retrieval $\rightarrow$ Worker |

---

## 2. Celery Task Contract

### Task Signature
```python
@celery_app.task(name="orcas.run_complaint", acks_late=True)
def run_complaint(run_id: str) -> None:
    """
    Executes the multi-agent analysis and verification pipeline for a queued complaint.
    
    Args:
        run_id (str): UUID corresponding to an existing record in the PostgreSQL runs table.
    """
```

### Invariants
1. `acks_late=True`: The worker acknowledges the message only after processing completes to survive container restarts.
2. Idempotency: Running `orcas.run_complaint` with the same `run_id` twice must not corrupt the run or emit conflicting terminal determinations.

---

## 3. Run Event Kinds (`RunStore.append_event`)

Every agent step and significant lifecycle milestone writes an immutable event row to PostgreSQL `run_events` to fulfill requirement **D5 (Run Reconstruction)**.

### Common Event Fields
All events contain:
* `run_id` (str, UUID)
* `seq` (int, strictly incrementing sequence number)
* `ts` (str, ISO 8601 UTC timestamp)
* `agent` (str, e.g., `"researcher"`, `"verifier"`, `"system"`)
* `step` (str, current graph node or operation)
* `kind` (str, one of the event kinds below)
* `latency_ms` (int, operation duration)
* `payload` (dict, kind-specific data)

### Event Kinds
1. `run_started`: Emitted when worker begins processing a run.
2. `llm_call`: Prompt tokens, completion tokens, model, latency, cost.
3. `tool_call`: Tool name, arguments supplied.
4. `retrieval`: Query text, filters applied, passage chunk IDs returned.
5. `reflect`: Researcher reflection analysis and query generation rationale.
6. `draft`: Candidate routing determination and drafted basis text.
7. `verify`: Claim decomposition, grounding check results, verification decision.
8. `retry`: Rotation to a new retry strategy (e.g. framework swap, query rewrite).
9. `escalate`: Escalation to human officer with reason code.
10. `guardrail`: Ingestion or output filter trigger.
11. `cache_hit`: Cache lookup hit for whole-run or retrieval cache.
12. `degraded`: Fallback model or stub used due to provider unavailability.
13. `run_finished`: Terminal status reached (`completed`, `escalated`, or `failed`).
