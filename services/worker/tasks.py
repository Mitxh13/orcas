import logging
import os
from typing import Any, Dict, Optional

from opentelemetry import propagate, trace

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
from services.worker.clients.llm_client import LLMClient
from services.worker.clients.observed_retrieval import ObservedRetrievalClient
from services.worker.clients.retrieval import HttpRetrievalClient
from services.worker.guardrails.output_filter import OutputFilter

logger = logging.getLogger("worker.tasks")

# Celery app (Requirement A5 – task queue integration).
# Import is optional so tests work without celery installed;
# the Docker image installs it via requirements.txt.
try:
    from celery import Celery

    celery_app = Celery(
        "orcas",
        broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    )
    celery_app.conf.task_acks_late = True
    celery_app.conf.task_default_queue = "orcas_tasks"
    celery_app.conf.task_reject_on_worker_lost = True
    celery_app.conf.worker_prefetch_multiplier = 1
    celery_app.conf.broker_transport_options = {"visibility_timeout": 180}
    celery_app.conf.task_time_limit = 90
    configure_tracing("worker", instrument_httpx=True)

    @celery_app.task(name="orcas.run_complaint", bind=True, max_retries=0, acks_late=True)
    def run_complaint(self, run_id: str) -> Dict[str, Any]:
        """Celery task entrypoint – delegates to run_complaint_pipeline."""
        configure_logging(os.getenv("LOG_LEVEL", "INFO"))
        token = correlation_id.set(run_id)
        try:
            context = propagate.extract(self.request.headers or {})
            with trace.get_tracer(__name__).start_as_current_span("worker.run_complaint", context=context):
                return run_complaint_pipeline(run_id=run_id)
        finally:
            correlation_id.reset(token)

except ImportError as exc:
    if exc.name != "celery":
        raise
    celery_app = None
    logger.info("Celery not installed; run_complaint_pipeline available as plain function.")


global_run_store = InMemoryRunStore()


def _runtime_store():
    return PostgresRunStore() if os.getenv("DATABASE_URL") else global_run_store


def _retrieval_client(run_id: str | None = None):
    if os.getenv("RETRIEVAL_SERVICE_URL"):
        return HttpRetrievalClient(run_id=run_id)
    from services.worker.clients.fake_retrieval import FakeRetrievalClient
    return FakeRetrievalClient()


def run_complaint_pipeline(
    run_id: str,
    complaint_data: Optional[Dict[str, Any]] = None,
    run_store: Optional[RunStore] = None,
    researcher_graph: Optional[ResearcherGraph] = None,
    verifier_graph: Optional[VerifierGraph] = None,
    inject_bad_draft: bool = False
) -> Dict[str, Any]:
    """
    Complete multi-agent pipeline orchestrating:
    1. Researcher agent exploration & drafting.
    2. Optional planted-bad-draft fault injection (Demo item #6).
    3. Independent Verifier audit & citation substring resolution.
    4. Guardrail output filtering.
    5. Event emission for full run reconstruction (Requirement D5).
    """
    store = run_store or _runtime_store()
    def record_llm(agent, step, prompt_version, result, latency_ms):
        usage = result.get("usage", {})
        store.append_event(
            run_id, agent, step, "llm_call", latency_ms=latency_ms,
            payload={"prompt_version": prompt_version, "model": result.get("model"),
                     "degraded": result.get("degraded", False)},
            tokens={"prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0)},
            cost_usd=float(usage.get("cost_usd", 0)),
        )

    retrieval = ObservedRetrievalClient(_retrieval_client(run_id), store, run_id)
    r_graph = researcher_graph or ResearcherGraph(
        retrieval_client=retrieval, llm_client=LLMClient(run_id=run_id, on_call=record_llm)
    )
    v_graph = verifier_graph or VerifierGraph(retrieval_client=r_graph.retrieval, llm_client=r_graph.llm)

    record = store.get_run(run_id)
    if not record and complaint_data:
        record = store.create_run(run_id, complaint_data)

    if not record:
        raise ValueError(f"Run ID {run_id} not found in run store.")

    if record["status"] in TERMINAL:
        return record.get("result") or record
    if not store.claim_run(run_id):
        return store.get_run(run_id)
    store.append_event(
        run_id=run_id,
        agent="system",
        step="intake",
        kind="run_started",
        payload={"complaint_id": run_id}
    )

    try:
        complaint_payload = dict(record.get("payload", {}))
        cache_key = complaint_payload.pop("_cache_key", None)
        
        # 1. Researcher Phase
        draft = r_graph.run(run_id=run_id, complaint_fields=complaint_payload)
        
        store.append_event(
            run_id=run_id,
            agent="researcher",
            step="draft",
            kind="draft",
            payload={"route": draft.get("route"), "basis": draft.get("basis")}
        )

        # Fault injection hook for Demo #6
        if inject_bad_draft or os.getenv("ORCAS_INJECT_BAD_DRAFT") == "1":
            logger.info("Injecting fabricated citation into draft for Verifier test.")
            draft["citations"].append({
                "citation_id": "fabricated_bad_chunk",
                "doc_id": "fabricated_doc",
                "doc_title": "Fake Act",
                "circular_ref": "FAKE/2026/01",
                "clause": "99",
                "chunk_id": "non_existent_chunk_hash_123456",
                "quote_span": "This phrase does not exist in any regulatory document."
            })

        # 2. Independent Verifier Phase
        evidence_ledger = draft.get("citations", [])
        attempt = 1
        max_attempts = 2
        v_res = v_graph.verify(draft=draft, evidence_ledger=evidence_ledger, attempt=attempt, max_retries=max_attempts)
        
        while v_res.get("decision") == "retry" and attempt < max_attempts:
            attempt += 1
            store.append_event(
                run_id=run_id,
                agent="verifier",
                step="retry",
                kind="retry",
                payload={"attempt": attempt, "strategy": v_res.get("suggested_strategy")}
            )
            v_res = v_graph.verify(draft=draft, evidence_ledger=evidence_ledger, attempt=attempt, max_retries=max_attempts)

        store.append_event(
            run_id=run_id,
            agent="verifier",
            step="verify",
            kind="verify",
            payload={"decision": v_res.get("decision"), "passed": v_res.get("passed")}
        )

        final_determination = v_res.get("draft")
        if not final_determination or v_res.get("decision") != "accept":
            final_determination = v_graph._escalate_draft(
                draft,
                reason_code="unverified",
                reason=v_res.get("reason", "Verification failed after retries."),
                attempt=attempt
            )["draft"]

        # 3. Output Guardrail Filtering
        is_safe, filtered_det, guardrail_msg = OutputFilter.filter(final_determination)
        if guardrail_msg:
            store.append_event(
                run_id=run_id,
                agent="guardrail",
                step="filter",
                kind="guardrail",
                payload={"message": guardrail_msg}
            )

        status = filtered_det.get("status", "completed")
        store.complete(run_id, filtered_det)
        verification = filtered_det.get("verification") or {}
        try:
            store.add_eval_score(run_id, "verification_passed",
                                 float(bool(verification.get("passed"))),
                                 {"source": "worker_verifier"})
        except Exception:
            logger.exception("unable to persist verification score")
        if cache_key and os.getenv("REDIS_URL") and status in {"completed", "escalated"}:
            try:
                RedisRunCache(os.environ["REDIS_URL"], int(os.getenv("RUN_CACHE_TTL_S", "3600"))).put(
                    cache_key, filtered_det
                )
            except Exception:
                logger.exception("unable to cache completed run")

        store.append_event(
            run_id=run_id,
            agent="system",
            step="finalize",
            kind="run_finished",
            payload={"status": status, "route": filtered_det.get("route")}
        )

        return filtered_det

    except Exception as exc:
        logger.error(f"Execution failed for run {run_id}: {exc}", exc_info=True)
        store.fail(run_id, str(exc))
        store.append_event(
            run_id=run_id,
            agent="system",
            step="error",
            kind="run_finished",
            payload={"status": "failed", "error": str(exc)}
        )
        raise
