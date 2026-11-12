"""
Worker pipeline orchestration for ORCAS.

Combines:
- Multi-agent coordination (Researcher + Independent Verifier)
- Infrastructure resilience & fail-closed escalation (M1, M2)
- Independent citation audit and ledger check (M6, M7)
- OpenTelemetry tracing, correlation ID propagation, and run caching (Platform)
- Output guardrail filtering and invariant enforcement (M9)
"""

import logging
import os
from typing import Any, Dict, Optional

from opentelemetry import propagate, trace

from libs.orcas_common.config import celery_broker_url, celery_result_backend
from libs.orcas_common.logging import configure_logging, correlation_id
from libs.orcas_common.run_cache import RedisRunCache
from libs.orcas_common.runstore import (
    TERMINAL,
    InMemoryRunStore,
    PostgresRunStore,
    RunStore,
)
from libs.orcas_common.telemetry import configure_tracing
from services.worker.agent.researcher import ResearcherGraph
from services.worker.agent.verifier import VerifierGraph
from services.worker.clients.llm_client import LLMClient, LLMUnavailable
from services.worker.clients.observed_retrieval import ObservedRetrievalClient
from services.worker.clients.retrieval import HttpRetrievalClient
from services.worker.guardrails.output_filter import OutputFilter

logger = logging.getLogger("worker.tasks")

# Celery app (Requirement A5 — task queue integration)
try:
    from celery import Celery

    _CELERY_SOFT_LIMIT = int(os.getenv("CELERY_SOFT_TIME_LIMIT", "120"))
    _CELERY_HARD_LIMIT = int(os.getenv("CELERY_TIME_LIMIT", "180"))

    celery_app = Celery(
        "orcas",
        broker=celery_broker_url(),
        backend=celery_result_backend(),
    )
    celery_app.conf.task_acks_late = True
    celery_app.conf.task_default_queue = "orcas_tasks"
    celery_app.conf.task_reject_on_worker_lost = True
    celery_app.conf.worker_prefetch_multiplier = 1
    celery_app.conf.broker_transport_options = {"visibility_timeout": _CELERY_HARD_LIMIT + 60}
    celery_app.conf.task_soft_time_limit = _CELERY_SOFT_LIMIT
    celery_app.conf.task_time_limit = _CELERY_HARD_LIMIT
    celery_app.conf.beat_schedule = {
        "sweep-stale-runs": {"task": "orcas.sweep_stale_runs", "schedule": 60.0},
    }
    configure_tracing("worker", instrument_httpx=True)

    @celery_app.task(name="orcas.run_complaint", bind=True, max_retries=0, acks_late=True)
    def run_complaint(self, run_id: str) -> Dict[str, Any]:
        """Celery task entrypoint — delegates to run_complaint_pipeline."""
        configure_logging(os.getenv("LOG_LEVEL", "INFO"))
        token = correlation_id.set(run_id)
        try:
            context = propagate.extract(self.request.headers or {})
            with trace.get_tracer(__name__).start_as_current_span(
                "worker.run_complaint", context=context
            ) as span:
                span.set_attribute("orcas.run_id", run_id)
                delivery_info = self.request.delivery_info or {}
                return run_complaint_pipeline(
                    run_id=run_id, redelivered=bool(delivery_info.get("redelivered")),
                )
        finally:
            correlation_id.reset(token)

    @celery_app.task(name="orcas.sweep_stale_runs")
    def sweep_stale_runs_task() -> int:
        return sweep_stale_runs(PostgresRunStore())

except ImportError as exc:
    if getattr(exc, "name", None) != "celery":
        raise
    celery_app = None
    logger.info("Celery not installed; run_complaint_pipeline available as plain function.")


global_run_store = InMemoryRunStore()
_MAX_VERIFY_RETRIES = int(os.getenv("MAX_VERIFY_RETRIES", "2"))


def _runtime_store() -> RunStore:
    return PostgresRunStore() if os.getenv("DATABASE_URL") else global_run_store


def _retrieval_client(run_id: str | None = None):
    if os.getenv("RETRIEVAL_SERVICE_URL"):
        return HttpRetrievalClient(run_id=run_id)
    from services.worker.clients.fake_retrieval import FakeRetrievalClient

    return FakeRetrievalClient()


def _strip_internal(d: Dict[str, Any]) -> Dict[str, Any]:
    """Remove internal-only keys before schema validation / storage."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _build_degraded_escalation(
    run_id: str,
    draft: Optional[Dict[str, Any]],
    reason_code: str,
    reason: str,
    partial_findings: list,
) -> Dict[str, Any]:
    """Return a minimal valid escalation determination with degraded=True."""
    base = {k: v for k, v in draft.items() if not k.startswith("_")} if draft else {}
    base.update({
        "run_id": run_id,
        "status": "escalated",
        "route": "escalate_to_human",
        "regulator": base.get("regulator", "UNDETERMINED"),
        "entity_type": base.get("entity_type", "other"),
        "timeline": None,
        "basis": f"Escalated due to infrastructure failure: {reason}",
        "citations": [],
        "escalation": {
            "reason_code": reason_code,
            "reason": reason,
            "partial_findings": partial_findings,
        },
        "verification": {
            "passed": False,
            "claims_checked": 0,
            "claims_supported": 0,
            "attempts": 1,
        },
        "prompt_versions": base.get("prompt_versions", {}),
        "index_version": base.get("index_version", "unknown"),
        "degraded": True,
        "disposed": False,
    })
    return _strip_internal(base)


def sweep_stale_runs(store: RunStore, max_age_s: int | None = None) -> int:
    """End orphaned runs after the worker hard limit and a recovery margin."""
    max_age_s = max_age_s or int(os.getenv("CELERY_TIME_LIMIT", "180")) + 60
    count = 0
    for run_id in store.stale_running_runs(max_age_s):
        escalation = _build_degraded_escalation(
            run_id, None, "agent_failure", "worker attempt exceeded its time limit", [],
        )
        store.complete(run_id, escalation)
        if store.get_run(run_id).get("result") == escalation:
            store.append_event(
                run_id, "system", "recovery", "run_finished",
                payload={"status": "escalated", "reason_code": "agent_failure", "degraded": True},
            )
            count += 1
    return count


def run_complaint_pipeline(
    run_id: str,
    complaint_data: Optional[Dict[str, Any]] = None,
    run_store: Optional[RunStore] = None,
    researcher_graph: Optional[ResearcherGraph] = None,
    verifier_graph: Optional[VerifierGraph] = None,
    inject_bad_draft: bool = False,
    redelivered: bool = False,
) -> Dict[str, Any]:
    """
    Complete multi-agent pipeline:
    1. Researcher agent exploration & drafting.
    2. Optional planted-bad-draft fault injection (Demo #6).
    3. Independent Verifier audit & citation resolution.
    4. Guardrail output filtering.
    5. Event emission for full run reconstruction (Requirement D5).
    """
    store = run_store or _runtime_store()

    def record_llm(agent, step, prompt_version, result, latency_ms):
        usage = result.get("usage", {})
        store.append_event(
            run_id,
            agent,
            step,
            "llm_call",
            latency_ms=latency_ms,
            payload={
                "prompt_version": prompt_version,
                "model": result.get("model"),
                "degraded": result.get("degraded", False),
            },
            tokens={
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
            },
            cost_usd=float(usage.get("cost_usd", 0)),
        )

    retrieval = ObservedRetrievalClient(_retrieval_client(run_id), store, run_id)
    r_graph = researcher_graph or ResearcherGraph(
        retrieval_client=retrieval,
        llm_client=LLMClient(run_id=run_id, on_call=record_llm),
    )
    v_graph = verifier_graph or VerifierGraph(
        retrieval_client=r_graph.retrieval, llm_client=r_graph.llm
    )

    record = store.get_run(run_id)
    if not record and complaint_data:
        record = store.create_run(run_id, complaint_data)

    if not record:
        store.fail(run_id, f"Run ID {run_id} not found in run store.")
        raise ValueError(f"Run ID {run_id} not found in run store.")

    if record.get("status") in TERMINAL:
        return record.get("result") or record
    if hasattr(store, "claim_run") and not store.claim_run(run_id, redelivered=redelivered):
        return store.get_run(run_id)

    store.update_status(run_id, "running")
    store.append_event(
        run_id=run_id,
        agent="system",
        step="intake",
        kind="run_started",
        payload={"complaint_id": run_id},
    )

    complaint_payload = dict(record.get("payload", {}))
    cache_key = complaint_payload.pop("_cache_key", None)
    complaint_payload.pop("_input_abuse_detected", None)
    draft: Optional[Dict[str, Any]] = None
    partial_findings: list = []

    # ------------------------------------------------------------------ #
    # Phase 1 — Researcher                                                 #
    # ------------------------------------------------------------------ #
    try:
        draft = r_graph.run(run_id=run_id, complaint_fields=complaint_payload)
        partial_findings = draft.get("citations", [])

        store.append_event(
            run_id=run_id,
            agent="researcher",
            step="draft",
            kind="draft",
            payload={
                "route": draft.get("route"),
                "basis": draft.get("basis"),
                "degraded": draft.get("degraded", False),
            },
        )

    except LLMUnavailable as exc:
        logger.warning("Researcher LLMUnavailable for run %s: %s", run_id, exc)
        escalation = _build_degraded_escalation(
            run_id=run_id,
            draft=None,
            reason_code="agent_failure",
            reason=str(exc),
            partial_findings=[],
        )
        store.append_event(
            run_id=run_id,
            agent="system",
            step="error",
            kind="degraded",
            payload={
                "reason_code": "agent_failure",
                "agent": exc.agent,
                "step": exc.step,
                "error": str(exc),
            },
        )
        store.complete(run_id, escalation)
        store.append_event(
            run_id=run_id,
            agent="system",
            step="finalize",
            kind="run_finished",
            payload={"status": "escalated", "route": "escalate_to_human", "degraded": True},
        )
        return escalation

    # ------------------------------------------------------------------ #
    # Fault injection hook for Demo #6                                     #
    # ------------------------------------------------------------------ #
    if inject_bad_draft or os.getenv("ORCAS_INJECT_BAD_DRAFT") == "1":
        logger.info("Injecting fabricated citation into draft for Verifier test.")
        draft["citations"] = list(draft.get("citations", []))
        draft["citations"].append({
            "citation_id": "fabricated_bad_chunk",
            "doc_id": "fabricated_doc",
            "doc_title": "Fake Act",
            "circular_ref": "FAKE/2026/01",
            "clause": "99",
            "chunk_id": "non_existent_chunk_hash_123456",
            "quote_span": "This phrase does not exist in any regulatory document.",
        })

    # ------------------------------------------------------------------ #
    # Phase 2 — Verifier                                                   #
    # ------------------------------------------------------------------ #
    evidence_ledger = draft.get("_evidence_ledger", draft.get("citations", []))
    max_attempts = _MAX_VERIFY_RETRIES
    attempt = 1
    v_res: Optional[Dict[str, Any]] = None

    try:
        v_res = v_graph.verify(
            draft=draft,
            evidence_ledger=evidence_ledger,
            attempt=attempt,
            max_retries=max_attempts,
        )

        while v_res.get("decision") == "retry" and attempt < max_attempts:
            attempt += 1
            store.append_event(
                run_id=run_id,
                agent="verifier",
                step="retry",
                kind="retry",
                payload={"attempt": attempt, "strategy": v_res.get("suggested_strategy")},
            )
            v_res = v_graph.verify(
                draft=draft,
                evidence_ledger=evidence_ledger,
                attempt=attempt,
                max_retries=max_attempts,
            )

    except LLMUnavailable as exc:
        logger.warning("Verifier LLMUnavailable for run %s: %s", run_id, exc)
        escalation = _build_degraded_escalation(
            run_id=run_id,
            draft=draft,
            reason_code="verification_unavailable",
            reason=str(exc),
            partial_findings=partial_findings,
        )
        store.append_event(
            run_id=run_id,
            agent="system",
            step="error",
            kind="degraded",
            payload={
                "reason_code": "verification_unavailable",
                "agent": exc.agent,
                "step": exc.step,
                "error": str(exc),
            },
        )
        store.complete(run_id, escalation)
        store.append_event(
            run_id=run_id,
            agent="system",
            step="finalize",
            kind="run_finished",
            payload={"status": "escalated", "route": "escalate_to_human", "degraded": True},
        )
        return escalation

    store.append_event(
        run_id=run_id,
        agent="verifier",
        step="verify",
        kind="verify",
        payload={
            "decision": v_res.get("decision"),
            "passed": v_res.get("passed"),
            "attempt": attempt,
            "strategy": v_res.get("suggested_strategy"),
        },
    )

    # Finalise determination
    final_determination = v_res.get("draft")
    if not final_determination or v_res.get("decision") != "accept":
        final_determination = v_graph._escalate_draft(
            draft,
            reason_code="unverified",
            reason=v_res.get("reason", "Verification failed after retries."),
            attempt=attempt,
        )["draft"]

    final_determination = _strip_internal(final_determination)

    # ------------------------------------------------------------------ #
    # Phase 3 — Output Guardrail                                           #
    # ------------------------------------------------------------------ #
    is_safe, filtered_det, guardrail_msg = OutputFilter.filter(final_determination)
    filtered_det = _strip_internal(filtered_det)

    if guardrail_msg:
        store.append_event(
            run_id=run_id,
            agent="guardrail",
            step="filter",
            kind="guardrail",
            payload={"message": guardrail_msg, "is_safe": is_safe},
        )

    status = filtered_det.get("status", "completed")
    store.complete(run_id, filtered_det)

    # Persist verification evaluation score
    verification = filtered_det.get("verification") or {}
    try:
        if hasattr(store, "add_eval_score"):
            store.add_eval_score(
                run_id,
                "verification_passed",
                float(bool(verification.get("passed"))),
                {"source": "worker_verifier"},
            )
    except Exception:
        logger.exception("unable to persist verification score")

    # Cache completed run in Redis if cache_key present
    if cache_key and os.getenv("REDIS_URL") and status in {"completed", "escalated"}:
        try:
            ttl = int(os.getenv("RUN_CACHE_TTL_S", "3600"))
            RedisRunCache(os.environ["REDIS_URL"], ttl).put(cache_key, filtered_det)
        except Exception:
            logger.exception("unable to cache completed run")

    store.append_event(
        run_id=run_id,
        agent="system",
        step="finalize",
        kind="run_finished",
        payload={"status": status, "route": filtered_det.get("route")},
    )

    return filtered_det
