"""
ORCAS Verifier Agent (M7 fixes).

Changes vs previous version
-----------------------------
* Citation is rejected unless its ``chunk_id`` is in this run's evidence
  ledger (not just anywhere in the index).
* Quote span must be at least 20 characters; empty or short spans are
  rejected.
* Scope-card chunks (``chunk_type=scope_card``) are rejected.
* ``passed`` defaults to ``False``, not ``True``, when the LLM omits it.
* ``claims_checked`` and ``claims_supported`` are taken from the LLM's
  ``claims`` list, not just len(citations).
* Route/evidence consistency:
  - ``refer_to_other_regulator`` needs a cited passage from the target
    regulator's framework.
  - ``assign_to_entity`` needs SEBI evidence.
  - If a ``timeline.days`` value does not appear in the basis-citation's
    text, the timeline is dropped and noted in ``basis``.
* Verifier uses a read-only client that only exposes ``get_chunk`` (A2).
* Prompt version read from env ``PROMPT_VERIFIER`` (M13).
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from services.worker.clients.fake_retrieval import FakeRetrievalClient
from services.worker.clients.llm_client import LLMClient
from prompts.loader import prompt_loader

logger = logging.getLogger("worker.verifier")

_V_VERIFIER = os.getenv("PROMPT_VERIFIER", "v1")
_MIN_QUOTE_LEN = 20


class _ReadOnlyRetrievalClient:
    """
    Wraps FakeRetrievalClient to expose only ``get_chunk``.
    The verifier must not perform corpus searches (A2 — separate tool perms).
    """

    def __init__(self, inner: FakeRetrievalClient):
        self._inner = inner

    def get_chunk(self, chunk_id: str, quote_span: str = "") -> Optional[Dict[str, Any]]:
        return self._inner.get_chunk(chunk_id, quote_span=quote_span)


class VerifierGraph:
    """
    ORCAS Verifier Agent (Requirement A2, A3).
    Audits candidate drafts with strictly read-only tool permissions.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        retrieval_client: Optional[FakeRetrievalClient] = None,
    ):
        self.llm = llm_client or LLMClient()
        # Wrap in read-only client so the verifier can only call get_chunk
        raw_retrieval = retrieval_client or FakeRetrievalClient()
        self.retrieval = _ReadOnlyRetrievalClient(raw_retrieval)
        # Keep a reference to the full retrieval for callers that need it
        self._full_retrieval = raw_retrieval

    def verify(
        self,
        draft: Dict[str, Any],
        evidence_ledger: List[Dict[str, Any]],
        attempt: int = 1,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """
        Audit a candidate draft.

        1. Each citation must be in this run's evidence ledger.
        2. Quote span must be ≥ 20 chars and a substring of the chunk.
        3. No scope-card citations.
        4. LLM claim-grounding gate.
        5. Route/evidence consistency checks.
        """
        # Already escalated — pass through
        if draft.get("status") == "escalated":
            return {
                "decision": "accept",
                "passed": False,
                "draft": draft,
                "reason": "Draft already marked as escalated.",
            }

        citations = draft.get("citations", [])
        if not citations:
            return self._escalate_draft(
                draft,
                reason_code="unverified",
                reason="Candidate draft contains zero citations.",
                attempt=attempt,
            )

        # Build ledger index by chunk_id for fast lookup
        ledger_chunk_ids = {p.get("chunk_id") for p in evidence_ledger}

        # ------------------------------------------------------------------ #
        # 1–3. Per-citation structural checks                                   #
        # ------------------------------------------------------------------ #
        for cit in citations:
            chunk_id = cit.get("chunk_id")
            quote_span = cit.get("quote_span", "")

            # 1. Citation must be in this run's ledger (not just anywhere in index)
            if chunk_id not in ledger_chunk_ids:
                logger.warning("Citation %s not in evidence ledger for this run.", chunk_id)
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "query_rewrite",
                    f"Citation {chunk_id} was not retrieved in this run's evidence ledger.",
                )

            # 2. Quote span must be at least 20 chars (not empty)
            if not quote_span or len(quote_span.strip()) < _MIN_QUOTE_LEN:
                logger.warning("Quote span for %s is absent or too short (%d chars).", chunk_id, len(quote_span.strip()))
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "query_rewrite",
                    f"Quote span for {chunk_id} is absent or shorter than {_MIN_QUOTE_LEN} chars.",
                )

            chunk = self.retrieval.get_chunk(chunk_id, quote_span=quote_span)
            if not chunk:
                logger.warning("Citation chunk_id %s did not resolve in index.", chunk_id)
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "query_rewrite",
                    f"Citation {chunk_id} not found in index.",
                )

            # 3. Reject scope-card citations
            if chunk.get("chunk_type") == "scope_card":
                logger.warning("Citation %s is a scope_card — not citable evidence.", chunk_id)
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "query_rewrite",
                    f"Citation {chunk_id} is a scope_card and cannot be used as evidence.",
                )

            # Quote span substring check (exact, then whitespace-normalised)
            chunk_text = chunk.get("text", "")
            span = quote_span.strip()
            if span not in chunk_text:
                # Whitespace-normalised fallback
                normalised_span = re.sub(r"\s+", " ", span)
                normalised_text = re.sub(r"\s+", " ", chunk_text)
                if normalised_span not in normalised_text:
                    logger.warning("Quote span '%s' not found in chunk %s.", span, chunk_id)
                    return self._retry_or_escalate(
                        draft,
                        attempt,
                        max_retries,
                        "query_rewrite",
                        f"Quote span for {chunk_id} not found in retrieved text.",
                    )

        # ------------------------------------------------------------------ #
        # 4. LLM claim-grounding gate                                          #
        # ------------------------------------------------------------------ #
        prompt = prompt_loader.render(
            "verifier",
            _V_VERIFIER,
            draft_determination=json.dumps(
                {k: v for k, v in draft.items() if not k.startswith("_")}),
            retrieved_passages=json.dumps(self._passages_for_prompt(citations, evidence_ledger)),
        )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="verifier",
            step="verify",
            prompt_version=_V_VERIFIER,
        )
        content = response["choices"][0]["message"]["content"]
        ver_json = self.llm.extract_json(content)

        # Default to False — the LLM must explicitly pass (M7)
        passed = ver_json.get("passed", False)

        # Derive claims_checked / claims_supported from LLM's claims list
        claims = ver_json.get("claims", [])
        claims_checked = ver_json.get("claims_checked", len(claims) or len(citations))
        claims_supported = ver_json.get("claims_supported", sum(1 for c in claims if c.get("supported", False)))

        if not passed or ver_json.get("decision") != "accept":
            return self._retry_or_escalate(
                draft,
                attempt,
                max_retries,
                ver_json.get("suggested_strategy") or "framework_swap",
                "Claims failed independent grounding by verifier LLM.",
            )

        # ------------------------------------------------------------------ #
        # 5. Route / evidence consistency                                       #
        # ------------------------------------------------------------------ #
        route = draft.get("route", "")
        regulator = draft.get("regulator", "")

        # refer_to_other_regulator needs a passage from the target regulator's framework
        if route == "refer_to_other_regulator":
            has_target_evidence = any(
                p.get("framework") == regulator for p in evidence_ledger
            )
            if not has_target_evidence:
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "framework_swap",
                    f"route=refer_to_other_regulator but no evidence from {regulator} framework in ledger.",
                )

        # assign_to_entity needs SEBI evidence
        if route == "assign_to_entity":
            has_sebi_evidence = any(p.get("framework") == "SEBI" for p in evidence_ledger)
            if not has_sebi_evidence:
                return self._retry_or_escalate(
                    draft,
                    attempt,
                    max_retries,
                    "query_rewrite",
                    "route=assign_to_entity but no SEBI evidence in ledger.",
                )

        # Timeline consistency: days value must appear in the basis-citation text
        timeline = draft.get("timeline")
        if timeline and isinstance(timeline, dict):
            days = timeline.get("days")
            basis_cit_id = timeline.get("basis_citation_id")
            if days is not None and basis_cit_id:
                # Find the citation with this ID
                cit_map = {c.get("citation_id"): c for c in citations}
                basis_cit = cit_map.get(basis_cit_id)
                if basis_cit:
                    basis_chunk = self.retrieval.get_chunk(basis_cit.get("chunk_id", ""))
                    if basis_chunk:
                        if str(days) not in basis_chunk.get("text", ""):
                            logger.info(
                                "Timeline %d days not found in basis citation %s — dropping timeline.",
                                days,
                                basis_cit_id,
                            )
                            # Drop timeline rather than failing the whole determination
                            draft = dict(draft)
                            draft["timeline"] = None
                            if draft.get("basis"):
                                draft["basis"] += f" (Timeline of {days} days removed: not found in cited text.)"

        # ------------------------------------------------------------------ #
        # Accept                                                               #
        # ------------------------------------------------------------------ #
        verified_draft = dict(draft)
        verified_draft["verification"] = {
            "passed": True,
            "claims_checked": claims_checked,
            "claims_supported": claims_supported,
            "attempts": attempt,
            "strategy": ver_json.get("suggested_strategy"),
        }
        verified_draft["prompt_versions"] = {
            **verified_draft.get("prompt_versions", {}), "verifier": _V_VERIFIER}
        verified_draft["degraded"] = bool(draft.get("degraded") or response.get("degraded"))

        return {
            "decision": "accept",
            "passed": True,
            "draft": verified_draft,
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _passages_for_prompt(citations: List[Dict[str, Any]],
                             evidence_ledger: List[Dict[str, Any]], limit: int = 5):
        """Every cited passage first, then other evidence up to ``limit`` in total."""
        cited_ids = {c.get("chunk_id") for c in citations}
        cited = [p for p in evidence_ledger if p.get("chunk_id") in cited_ids]
        others = [p for p in evidence_ledger if p.get("chunk_id") not in cited_ids]
        return cited + others[:max(0, limit - len(cited))]

    def _retry_or_escalate(
        self,
        draft: Dict[str, Any],
        attempt: int,
        max_retries: int,
        strategy: str,
        reason: str,
    ) -> Dict[str, Any]:
        if attempt < max_retries:
            return {
                "decision": "retry",
                "passed": False,
                "suggested_strategy": strategy,
                "reason": reason,
            }
        return self._escalate_draft(draft, reason_code="unverified", reason=reason, attempt=attempt)

    def _escalate_draft(
        self,
        draft: Dict[str, Any],
        reason_code: str,
        reason: str,
        attempt: int,
    ) -> Dict[str, Any]:
        esc_draft = dict(draft)
        esc_draft["status"] = "escalated"
        esc_draft["route"] = "escalate_to_human"
        esc_draft["timeline"] = None
        esc_draft["escalation"] = {
            "reason_code": reason_code,
            "reason": reason,
            "partial_findings": draft.get("citations", []),
        }
        esc_draft["verification"] = {
            "passed": False,
            "claims_checked": len(draft.get("citations", [])),
            "claims_supported": 0,
            "attempts": attempt,
        }
        esc_draft["prompt_versions"] = {
            **esc_draft.get("prompt_versions", {}), "verifier": _V_VERIFIER}
        return {
            "decision": "escalate",
            "passed": False,
            "draft": esc_draft,
            "reason": reason,
        }
