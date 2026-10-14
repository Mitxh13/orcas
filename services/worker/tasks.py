import os
import uuid
import logging
from typing import Any, Dict, Optional

from services.worker.agent.researcher import ResearcherGraph
from services.worker.agent.verifier import VerifierGraph
from services.worker.guardrails.output_filter import OutputFilter
from services.worker.clients.runstore import InMemoryRunStore

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

    @celery_app.task(name="orcas.run_complaint", bind=True, max_retries=0, acks_late=True)
    def run_complaint(self, run_id: str, complaint_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Celery task entrypoint – delegates to run_complaint_pipeline."""
        return run_complaint_pipeline(run_id=run_id, complaint_data=complaint_data)

except ImportError:
    celery_app = None
    logger.info("Celery not installed; run_complaint_pipeline available as plain function.")


global_run_store = InMemoryRunStore()


def run_complaint_pipeline(
    run_id: str,
    complaint_data: Optional[Dict[str, Any]] = None,
    run_store: Optional[InMemoryRunStore] = None,
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
    store = run_store or global_run_store
    r_graph = researcher_graph or ResearcherGraph()
    v_graph = verifier_graph or VerifierGraph(retrieval_client=r_graph.retrieval, llm_client=r_graph.llm)

    record = store.get_run(run_id)
    if not record and complaint_data:
        record = store.create_run(run_id, complaint_data)

    if not record:
        raise ValueError(f"Run ID {run_id} not found in run store.")

    store.update_status(run_id, "running")
    store.append_event(
        run_id=run_id,
        agent="system",
        step="intake",
        kind="run_started",
        payload={"complaint_id": run_id}
    )

    try:
        complaint_payload = record.get("payload", {})
        
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
