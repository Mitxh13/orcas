import json
import logging
from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, START, END

from services.worker.clients.fake_retrieval import FakeRetrievalClient
from services.worker.clients.llm_client import LLMClient
from prompts.loader import prompt_loader

logger = logging.getLogger("worker.verifier")


class VerifierGraph:
    """
    LangGraph implementation of the ORCAS Verifier Agent (Requirement A2, A3).
    A separate graph with strictly read-only tools that audits candidate drafts.
    """
    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        retrieval_client: Optional[FakeRetrievalClient] = None
    ):
        self.llm = llm_client or LLMClient()
        self.retrieval = retrieval_client or FakeRetrievalClient()

    def verify(
        self,
        draft: Dict[str, Any],
        evidence_ledger: List[Dict[str, Any]],
        attempt: int = 1,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """
        Audits candidate draft:
        1. Checks citations resolve via get_chunk.
        2. Checks quote_span is an exact substring.
        3. Invokes LLM claim-checking for semantic grounding.
        4. Rejects or escalates unverified claims.
        """
        # If draft is already escalated, pass through
        if draft.get("status") == "escalated":
            return {
                "decision": "accept",
                "passed": False,
                "draft": draft,
                "reason": "Draft already marked as escalated."
            }

        citations = draft.get("citations", [])
        if not citations:
            return self._escalate_draft(
                draft,
                reason_code="unverified",
                reason="Candidate draft contains zero citations.",
                attempt=attempt
            )

        # 1. Resolve every citation chunk and verify quote_span
        for cit in citations:
            chunk_id = cit.get("chunk_id")
            quote_span = cit.get("quote_span", "")

            chunk = self.retrieval.get_chunk(chunk_id, quote_span=quote_span)
            if not chunk:
                # Citation does not resolve in vector database
                logger.warning(f"Citation chunk_id {chunk_id} did not resolve.")
                if attempt <= max_retries:
                    return {
                        "decision": "retry",
                        "passed": False,
                        "suggested_strategy": "query_rewrite",
                        "reason": f"Citation chunk {chunk_id} not found."
                    }
                else:
                    return self._escalate_draft(
                        draft,
                        reason_code="unverified",
                        reason=f"Citation {chunk_id} failed to resolve after {attempt} attempts.",
                        attempt=attempt
                    )

            # Check exact substring containment
            chunk_text = chunk.get("text", "")
            if quote_span.strip() and quote_span.strip() not in chunk_text:
                logger.warning(f"Quote span '{quote_span}' not in chunk text.")
                if attempt <= max_retries:
                    return {
                        "decision": "retry",
                        "passed": False,
                        "suggested_strategy": "query_rewrite",
                        "reason": f"Quote span for {chunk_id} not found in retrieved text."
                    }
                else:
                    return self._escalate_draft(
                        draft,
                        reason_code="unverified",
                        reason=f"Quote span for {chunk_id} failed substring check.",
                        attempt=attempt
                    )

        # 2. LLM Claim-Checking Gate
        prompt = prompt_loader.render(
            "verifier",
            "v1",
            draft_determination=json.dumps(draft),
            retrieved_passages=json.dumps(evidence_ledger[:5])
        )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="verifier",
            step="verify",
            prompt_version="v1"
        )
        content = response["choices"][0]["message"]["content"]
        ver_json = self.llm.extract_json(content)

        passed = ver_json.get("passed", True)
        if not passed or ver_json.get("decision") != "accept":
            if attempt <= max_retries:
                return {
                    "decision": "retry",
                    "passed": False,
                    "suggested_strategy": ver_json.get("suggested_strategy") or "framework_swap",
                    "reason": "Claims failed independent grounding."
                }
            else:
                return self._escalate_draft(
                    draft,
                    reason_code="unverified",
                    reason="Claims could not be grounded in retrieved passages after retries.",
                    attempt=attempt
                )

        # Accepted
        verified_draft = dict(draft)
        verified_draft["verification"] = {
            "passed": True,
            "claims_checked": len(citations),
            "claims_supported": len(citations),
            "attempts": attempt
        }
        verified_draft.setdefault("prompt_versions", {})["verifier"] = "v1"

        return {
            "decision": "accept",
            "passed": True,
            "draft": verified_draft
        }

    def _escalate_draft(
        self,
        draft: Dict[str, Any],
        reason_code: str,
        reason: str,
        attempt: int
    ) -> Dict[str, Any]:
        esc_draft = dict(draft)
        esc_draft["status"] = "escalated"
        esc_draft["route"] = "escalate_to_human"
        esc_draft["timeline"] = None
        esc_draft["escalation"] = {
            "reason_code": reason_code,
            "reason": reason,
            "partial_findings": draft.get("citations", [])
        }
        esc_draft["verification"] = {
            "passed": False,
            "claims_checked": len(draft.get("citations", [])),
            "claims_supported": 0,
            "attempts": attempt
        }
        esc_draft.setdefault("prompt_versions", {})["verifier"] = "v1"
        return {
            "decision": "escalate",
            "passed": False,
            "draft": esc_draft,
            "reason": reason
        }
