import asyncio
import json
import os
import re
import time
import uuid
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ORCAS LLM Stub Service", version="1.0.0")

STUB_DELAY_MS = int(os.getenv("STUB_DELAY_MS", "1500"))


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
    degraded: bool = True


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "stub", "delay_ms": STUB_DELAY_MS}


def generate_stub_reply(request: ChatRequest) -> str:
    """Generate realistic responses based on calling agent, step, and complaint text."""
    combined = " ".join([m.content for m in request.messages])
    step = (request.step or "").lower()
    
    # Extract complaint text from prompt
    complaint_match = re.search(r"complaint(?:\s*text)?:\s*(.*?)(?:\n\w+:|$)", combined, re.DOTALL | re.IGNORECASE)
    target_text = complaint_match.group(1).lower() if complaint_match else combined.lower()

    # --- Classification ---
    if "classify" in step:
        if any(w in target_text for w in ["insurance", "insurer", "hospital"]):
            return json.dumps({
                "entity_type": "other",
                "regulator": "UNDETERMINED",
                "product": "health_insurance",
                "reasoning": "Insurance products fall under IRDAI and are outside covered corpus."
            })
        elif any(w in target_text for w in ["fixed deposit", "term deposit", "bank app"]):
            return json.dumps({
                "entity_type": "bank",
                "regulator": "RBI",
                "product": "fixed_deposit",
                "reasoning": "Bank deposit complaints belong to RBI Integrated Ombudsman Scheme."
            })
        elif any(w in target_text for w in ["mutual fund", "nav", "amc", "redemption"]):
            return json.dumps({
                "entity_type": "mutual_fund",
                "regulator": "SEBI",
                "product": "mutual_fund_units",
                "reasoning": "Mutual fund servicing is governed under SEBI MF Regulations."
            })
        elif any(w in target_text for w in ["broker", "demat", "shares", "trading"]):
            return json.dumps({
                "entity_type": "broker",
                "regulator": "SEBI",
                "product": "equity_broking",
                "reasoning": "Stock brokers are registered SEBI intermediaries."
            })
        else:
            return json.dumps({
                "entity_type": "other",
                "regulator": "UNDETERMINED",
                "product": "general",
                "reasoning": "Unrecognized financial product."
            })

    # --- Reflection ---
    if "reflect" in step:
        if "bank" in target_text or "fixed deposit" in target_text:
            return json.dumps({
                "reflection": "Product identified as bank fixed deposit. Jurisdiction redirect from SEBI to RBI applies.",
                "requires_further_search": True,
                "framework_swap": True,
                "next_framework": "RBI",
                "next_query": "bank fixed deposit interest grievance deficiency in service"
            })
        return json.dumps({
            "reflection": "Retrieved evidence directly addresses servicing requirements.",
            "requires_further_search": False,
            "framework_swap": False,
            "next_framework": None,
            "next_query": None
        })

    # --- Draft Determination ---
    if "draft" in step:
        if "fixed deposit" in target_text or "bank" in target_text:
            return json.dumps({
                "route": "refer_to_other_regulator",
                "regulator": "RBI",
                "entity_type": "bank",
                "timeline": {"days": 30, "basis_citation_id": "c1"},
                "basis": "Bank fixed deposit interest dispute falls under the RBI Integrated Ombudsman Scheme (2021) for deficiencies in service [c1].",
                "citations": [
                    {
                        "citation_id": "c1",
                        "doc_id": "rbi_integrated_ombudsman_scheme_2021",
                        "doc_title": "Reserve Bank - Integrated Ombudsman Scheme, 2021",
                        "circular_ref": "RBI/2021-2022/126",
                        "clause": "9",
                        "chunk_id": "17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64",
                        "quote_span": "deficiency in service may file a complaint under the Scheme"
                    }
                ]
            })
        else:
            return json.dumps({
                "route": "assign_to_entity",
                "regulator": "SEBI",
                "entity_type": "broker",
                "timeline": {"days": 30, "basis_citation_id": "c1"},
                "basis": "Investor grievance falls under SEBI SCORES framework with 30-day resolution timeline [c1].",
                "citations": [
                    {
                        "citation_id": "c1",
                        "doc_id": "sebi_scores_master_circular",
                        "doc_title": "SEBI Master Circular on Investor Grievance Redressal",
                        "circular_ref": "SEBI/HO/OIAE/OIAE_IAD-1/P/CIR/2023/145",
                        "clause": "2",
                        "chunk_id": "d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e",
                        "quote_span": "registered intermediaries and recognised MIIs that such investor grievances may be resolved faster"
                    }
                ]
            })

    # --- Verifier Claim Check ---
    if "verify" in step:
        return json.dumps({
            "decision": "accept",
            "passed": True,
            "unsupported_claims": [],
            "suggested_strategy": None
        })

    return "OK"


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
                finish_reason="stop"
            )
        ],
        usage=ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=0.0
        ),
        model=request.model or "llm-stub",
        degraded=True
    )
