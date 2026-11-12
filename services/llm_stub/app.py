"""
ORCAS LLM Stub Service (M4 fixes).

Changes vs previous version
-----------------------------
* ``degraded`` is ``False`` in stub mode (the stub is the intended path when
  ``ORCAS_STUB_MODE=true``; the gateway sets ``degraded=True`` only when it
  falls back unexpectedly).
* Complaint-text extraction uses ``<complaint>…</complaint>`` delimiters
  (added by M11 prompt-injection isolation) with a regex fallback that stops
  at the next uppercase-word-colon section header to avoid running into the
  evidence ledger.
* Keyword sets extended per M4: registrar, depository, UPI, ATM, net banking,
  debit card, FD, listed company, etc.
* Split-regulator detection: RBI terms + SEBI-entity terms → returns two
  ``regulators_involved`` (see M8).
* Citation chooser: RBI → preamble/clause 9 (never clause 10 or
  non-maintainability); SEBI → SCORES clause.
* ``plan`` step now returns a valid JSON action.
* §4.1 chain: ``classify`` returns SEBI (stated_regulator); ``reflect``
  proposes the RBI swap.
* Unknown steps return a helpful ``{"status": "unrecognised_step"}`` dict
  instead of bare ``"OK"``.
"""

import asyncio
import json
import os
import re
import time
import uuid
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ORCAS LLM Stub Service", version="2.0.0")

STUB_DELAY_MS = int(os.getenv("STUB_DELAY_MS", "1500"))

# ---------------------------------------------------------------------------
# Pydantic models (unchanged)
# ---------------------------------------------------------------------------


_DECIDING_CLAUSES = {"RBI": ("preamble", "9")}


def _clause_of(item: dict) -> str:
    return str(item.get("clause_id") or item.get("clause") or "")


def _is_non_maintainability(item: dict) -> bool:
    heading = item.get("heading_path") or ""
    heading = " ".join(heading) if isinstance(heading, list) else str(heading)
    opening = f"{heading} {item.get('text', '')[:160]}".lower()
    return "non-maintainab" in opening or "non maintainab" in opening or (
        item.get("framework") == "RBI" and _clause_of(item) == "10")


def citation_from_evidence(prompt: str, framework: str) -> dict | None:
    """Pick an exact quote from retrieved evidence in the draft prompt."""
    match = re.search(r"Evidence Ledger:\s*(\[[^\r\n]*\])", prompt)
    if not match:
        return None
    try:
        ledger = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    candidates = [item for item in ledger if item.get("framework") == framework
                  and item.get("chunk_type", "clause") != "scope_card" and item.get("text")]
    if not candidates:
        return None
    keywords = ("regulated by reserve bank", "deficiency in service") if framework == "RBI" else (
        "investor grievances", "registered intermediaries", "mutual fund"
    )
    # The clauses that decide jurisdiction come first; a clause listing grounds on which a
    # complaint is NOT maintainable is cited only when nothing else was retrieved.
    deciding = _DECIDING_CLAUSES.get(framework, ())
    usable = [item for item in candidates if not _is_non_maintainability(item)] or candidates
    selected = next(
        (item for clause in deciding for item in usable if _clause_of(item) == clause),
        next((item for word in keywords for item in usable if word in item["text"].lower()),
             usable[0]),
    )
    passage = selected["text"]
    phrase = next((word for word in keywords if word in passage.lower()), "")
    start = max(0, passage.lower().find(phrase) - 30) if phrase else 0
    quote_span = passage[start:start + 180]
    return {
        "citation_id": "c1",
        "doc_id": selected.get("doc_id", ""),
        "doc_title": selected.get("doc_title", ""),
        "circular_ref": selected.get("circular_ref", ""),
        "clause": _clause_of(selected),
        "chunk_id": selected["chunk_id"],
        "quote_span": quote_span,
    }


class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = "llm-stub"
    temperature: Optional[float] = 0.0
    max_tokens: Optional[int] = 2048
    run_id: Optional[str] = None
    agent: Optional[str] = None
    step: Optional[str] = None
    prompt_version: Optional[str] = None


class ChatUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatResponse(BaseModel):
    id: str
    choices: List[ChatChoice]
    usage: ChatUsage
    model: str
    # In stub mode the stub is the *intended* path → not degraded.
    degraded: bool = False


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "stub", "delay_ms": STUB_DELAY_MS}


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

_RBI_TERMS = {
    "fixed deposit", "fd", "term deposit", "savings account", "bank account",
    "bank loan", "home loan", "upi", "atm", "net banking", "debit card",
    "bank app", "banking app",
}
_SEBI_BROKER = {"broker", "trading", "trade", "stock", "shares", "equity"}
_SEBI_MF = {"mutual fund", "mf", "nav", "amc", "redemption", "sip"}
_SEBI_REGISTRAR = {"registrar", "rta", "share transfer", "name update"}
_SEBI_LISTED = {"listed company", "dividend", "annual report", "shareholder"}
_SEBI_DEPOSITORY = {"depository", "demat", "dp", "dematerialisation"}
_OOC = {"insurance", "insurer", "hospital", "pension", "nps"}


def _has(text: str, terms: set) -> bool:
    """Whole-word match (plural allowed), so "rta" does not fire on "important"."""
    return any(re.search(rf"\b{re.escape(t)}(?:s|es)?\b", text) for t in terms)


def _extract_complaint_text(combined: str) -> str:
    """
    Extract the complaint text from the combined prompt content.

    Priority:
    1. Text between ``<complaint>`` and ``</complaint>`` tags (M11 isolation).
    2. Text after a ``Complaint Text:`` / ``complaint:`` label up to the next
       section header (all-caps word followed by colon, or a blank line).
    3. The whole combined string (last resort, as before).
    """
    # 1. XML delimiter
    m = re.search(r"<complaint>(.*?)</complaint>", combined, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()

    # 2. Section-label extraction — stop before next header or JSON block
    m = re.search(
        r"(?:complaint\s*(?:text)?|complaint_text)\s*:\s*(.*?)(?=\n[A-Z][a-zA-Z ]+:|```|\Z)",
        combined,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().lower()

    return combined.lower()


# ---------------------------------------------------------------------------
# Stub reply generator
# ---------------------------------------------------------------------------

# Known chunk IDs from tests/fixtures/retrieval_fixtures.json
_RBI_CHUNK_ID = "17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64"
_SEBI_CHUNK_ID = "d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e"


def generate_stub_reply(request: ChatRequest) -> str:
    """Generate realistic responses based on calling agent, step, and complaint text."""
    combined = " ".join([m.content for m in request.messages])
    step = (request.step or "").lower()
    target_text = _extract_complaint_text(combined)

    is_rbi = _has(target_text, _RBI_TERMS)
    is_broker = _has(target_text, _SEBI_BROKER)
    is_mf = _has(target_text, _SEBI_MF)
    is_registrar = _has(target_text, _SEBI_REGISTRAR)
    is_listed = _has(target_text, _SEBI_LISTED)
    is_depository = _has(target_text, _SEBI_DEPOSITORY)
    is_ooc = _has(target_text, _OOC)
    is_split = is_rbi and (is_broker or is_mf or is_registrar or is_listed or is_depository)

    # -------------------------------------------------------------------
    # classify
    # -------------------------------------------------------------------
    if "classify" in step:
        if is_ooc:
            return json.dumps({
                "entity_type": "other",
                "regulator": "UNDETERMINED",
                "product": "out_of_corpus",
                "stated_regulator": None,
                "reasoning": "Product is outside covered corpus (insurance/pension/NPS).",
            })
        if is_split:
            return json.dumps({
                "entity_type": "other",
                "regulator": "UNDETERMINED",
                "regulators_involved": ["SEBI", "RBI"],
                "product": "mixed",
                "stated_regulator": "SEBI" if _has(target_text, {"sebi"}) else None,
                "reasoning": "Complaint spans both RBI (banking) and SEBI (securities) jurisdiction.",
            })
        if is_rbi:
            # §4.1 chain: if the complaint mentions SEBI, honour stated_regulator=SEBI
            # and let reflect propose the swap
            stated = "SEBI" if _has(target_text, {"sebi"}) else None
            return json.dumps({
                "entity_type": "bank",
                "regulator": stated or "RBI",
                "product": "fixed_deposit" if _has(target_text, {"fixed deposit", "fd", "term deposit"}) else "bank_product",
                "stated_regulator": stated,
                "reasoning": "Bank product — RBI Integrated Ombudsman Scheme applies.",
            })
        if is_broker:
            return json.dumps({
                "entity_type": "broker",
                "regulator": "SEBI",
                "product": "equity_broking",
                "stated_regulator": None,
                "reasoning": "Stock broker — SEBI SCORES framework applies.",
            })
        if is_mf:
            return json.dumps({
                "entity_type": "mutual_fund",
                "regulator": "SEBI",
                "product": "mutual_fund_units",
                "stated_regulator": None,
                "reasoning": "Mutual fund — SEBI MF Regulations apply.",
            })
        if is_registrar:
            return json.dumps({
                "entity_type": "registrar",
                "regulator": "SEBI",
                "product": "registrar_services",
                "stated_regulator": None,
                "reasoning": "Registrar/RTA — SEBI jurisdiction.",
            })
        if is_listed:
            return json.dumps({
                "entity_type": "listed_company",
                "regulator": "SEBI",
                "product": "listed_company_services",
                "stated_regulator": None,
                "reasoning": "Listed company — SEBI jurisdiction.",
            })
        if is_depository:
            return json.dumps({
                "entity_type": "depository",
                "regulator": "SEBI",
                "product": "demat_services",
                "stated_regulator": None,
                "reasoning": "Depository / DP — SEBI jurisdiction.",
            })
        return json.dumps({
            "entity_type": "other",
            "regulator": "UNDETERMINED",
            "product": "general",
            "stated_regulator": None,
            "reasoning": "Unrecognized financial product.",
        })

    # -------------------------------------------------------------------
    # plan
    # -------------------------------------------------------------------
    if "plan" in step:
        if is_rbi or _has(target_text, {"bank", "fixed deposit", "fd"}):
            return json.dumps({
                "action": "search_corpus",
                "args": {
                    "query": "bank fixed deposit interest grievance deficiency in service",
                    "framework": "RBI",
                    "k": 3,
                },
            })
        return json.dumps({
            "action": "search_corpus",
            "args": {
                "query": "investor grievance redressal timeline",
                "framework": "SEBI",
                "k": 3,
            },
        })

    # -------------------------------------------------------------------
    # reflect
    # -------------------------------------------------------------------
    if "reflect" in step:
        if is_rbi and _has(target_text, {"sebi"}):
            # §4.1: complaint originally directed to SEBI, but product is RBI → propose swap
            return json.dumps({
                "reflection": (
                    "The product is a bank fixed deposit, which falls under the RBI Integrated "
                    "Ombudsman Scheme, not SEBI. Proposing framework swap to RBI."
                ),
                "requires_further_search": True,
                "framework_swap": True,
                "next_framework": "RBI",
                "next_query": "bank fixed deposit interest grievance deficiency in service RBI ombudsman",
            })
        if is_rbi:
            return json.dumps({
                "reflection": "Product identified as bank/RBI product; jurisdiction confirmed.",
                "requires_further_search": False,
                "framework_swap": False,
                "next_framework": None,
                "next_query": None,
            })
        return json.dumps({
            "reflection": "Retrieved evidence directly addresses the servicing requirements.",
            "requires_further_search": False,
            "framework_swap": False,
            "next_framework": None,
            "next_query": None,
        })

    # -------------------------------------------------------------------
    # draft
    # -------------------------------------------------------------------
    if "draft" in step:
        if is_ooc or is_split:
            reason = "split_regulators" if is_split else "out_of_corpus"
            return json.dumps({
                "route": "escalate_to_human",
                "regulator": "UNDETERMINED",
                "entity_type": "other",
                "timeline": None,
                "basis": f"Complaint escalated: {reason}.",
                "citations": [],
            })

        has_ledger = "Evidence Ledger:" in combined

        if is_rbi:
            citation = citation_from_evidence(combined, "RBI")
            if not citation and not has_ledger:
                citation = {
                    "citation_id": "c1",
                    "doc_id": "rbi_integrated_ombudsman_scheme_2021",
                    "doc_title": "Reserve Bank - Integrated Ombudsman Scheme, 2021",
                    "circular_ref": "RBI/2021-2022/126",
                    "clause": "9",
                    "chunk_id": _RBI_CHUNK_ID,
                    "quote_span": "deficiency in service may file a complaint under the Scheme",
                }
            citations = [citation] if citation else []
            basis = (
                "Bank fixed deposit interest dispute falls under the RBI Integrated "
                "Ombudsman Scheme, 2021 for deficiencies in service [c1]."
                if citation else "No RBI passage was retrieved for verification."
            )
            return json.dumps({
                "route": "refer_to_other_regulator",
                "regulator": "RBI",
                "entity_type": "bank",
                "timeline": None,
                "basis": basis,
                "citations": citations,
            })

        # SEBI default (broker / MF / registrar / listed / depository)
        if is_mf:
            entity_type, product_label = "mutual_fund", "mutual fund"
        elif is_registrar:
            entity_type, product_label = "registrar", "registrar"
        elif is_listed:
            entity_type, product_label = "listed_company", "listed company"
        elif is_depository:
            entity_type, product_label = "depository", "depository"
        else:
            entity_type, product_label = "broker", "broker"

        citation = citation_from_evidence(combined, "SEBI")
        if not citation and not has_ledger:
            citation = {
                "citation_id": "c1",
                "doc_id": "sebi_scores_master_circular",
                "doc_title": "SEBI Master Circular on Investor Grievance Redressal",
                "circular_ref": "SEBI/HO/OIAE/OIAE_IAD-1/P/CIR/2023/145",
                "clause": "2",
                "chunk_id": _SEBI_CHUNK_ID,
                "quote_span": (
                    "registered intermediaries and recognised MIIs that such investor "
                    "grievances may be resolved faster"
                ),
            }
        citations = [citation] if citation else []
        basis = (
            f"Investor grievance against {product_label} falls under SEBI SCORES framework [c1]."
            if citation else "No SEBI passage was retrieved for verification."
        )

        return json.dumps({
            "route": "assign_to_entity",
            "regulator": "SEBI",
            "entity_type": entity_type,
            "timeline": None,
            "basis": basis,
            "citations": citations,
        })

    # -------------------------------------------------------------------
    # verify
    # -------------------------------------------------------------------
    if "verify" in step:
        return json.dumps({
            "decision": "accept",
            "passed": True,
            "claims": [{"claim": "Routing determination supported by regulatory evidence.", "supported": True}],
            "claims_checked": 1,
            "claims_supported": 1,
            "unsupported_claims": [],
            "suggested_strategy": None,
        })

    # -------------------------------------------------------------------
    # Unknown step — return a structured response, not bare "OK"
    # -------------------------------------------------------------------
    return json.dumps({"status": "unrecognised_step", "step": request.step})


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@app.post("/v1/chat", response_model=ChatResponse)
async def chat_completion(request: ChatRequest):
    if STUB_DELAY_MS > 0:
        await asyncio.sleep(STUB_DELAY_MS / 1000.0)

    reply_content = generate_stub_reply(request)

    prompt_tokens = max(10, sum(len(m.content) for m in request.messages) // 4)
    completion_tokens = max(5, len(reply_content) // 4)

    return ChatResponse(
        id=f"stub-{uuid.uuid4().hex[:12]}",
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=reply_content),
                finish_reason="stop",
            )
        ],
        usage=ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=0.0,
        ),
        model=request.model or "llm-stub",
        degraded=False,  # stub is the intended path in stub mode
    )
