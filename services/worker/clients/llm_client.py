import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("worker.llm_client")


class LLMClient:
    """Client for calling the ORCAS LLM Gateway from worker agents."""
    def __init__(self, gateway_url: Optional[str] = None, run_id: Optional[str] = None,
                 on_call=None):
        self.gateway_url = gateway_url or os.getenv("LLM_GATEWAY_URL", "http://localhost:8002")
        self.run_id = run_id
        self.on_call = on_call

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        run_id: Optional[str] = None,
        agent: Optional[str] = None,
        step: Optional[str] = None,
        prompt_version: Optional[str] = None,
        timeout: float = 15.0
    ) -> Dict[str, Any]:
        started = time.monotonic()
        payload = {
            "messages": messages,
            "temperature": temperature,
            "run_id": run_id or self.run_id,
            "agent": agent,
            "step": step,
            "prompt_version": prompt_version
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                headers = {"X-Correlation-ID": run_id or self.run_id} if run_id or self.run_id else {}
                resp = client.post(f"{self.gateway_url}/v1/chat", json=payload, headers=headers)
                if resp.status_code == 200:
                    result = resp.json()
                    self._record_call(agent, step, prompt_version, result, started)
                    return result
                logger.warning("LLM Gateway returned status %d; falling back to heuristic.", resp.status_code)
        except Exception as exc:
            logger.warning("LLM Gateway unreachable (%s); falling back to local heuristic.", exc)

        result = self._local_heuristic_chat(messages, agent, step)
        self._record_call(agent, step, prompt_version, result, started)
        return result

    def _record_call(self, agent, step, prompt_version, result, started):
        if self.on_call:
            self.on_call(agent or "unknown", step or "unknown", prompt_version or "unknown",
                         result, round((time.monotonic() - started) * 1000))

    def _local_heuristic_chat(
        self,
        messages: List[Dict[str, str]],
        agent: Optional[str],
        step: Optional[str]
    ) -> Dict[str, Any]:
        """Provides offline heuristic responses when gateway is not running."""
        combined = " ".join([m.get("content", "") for m in messages])
        complaint_match = re.search(r"complaint(?:\s*text)?:\s*(.*?)(?:\n\w+:|$)", combined, re.DOTALL | re.IGNORECASE)
        target_text = complaint_match.group(1).lower() if complaint_match else combined.lower()

        content = "{}"

        if "classify" in (step or ""):
            if any(w in target_text for w in ["insurance", "insurer", "hospital"]):
                content = json.dumps({
                    "entity_type": "other",
                    "regulator": "UNDETERMINED",
                    "product": "health_insurance",
                    "reasoning": "Insurance products fall under IRDAI and are outside covered corpus."
                })
            elif any(w in target_text for w in ["fixed deposit", "term deposit", "bank app"]):
                content = json.dumps({
                    "entity_type": "bank",
                    "regulator": "RBI",
                    "product": "fixed_deposit",
                    "reasoning": "Bank deposit complaints belong to RBI Integrated Ombudsman Scheme."
                })
            elif any(w in target_text for w in ["mutual fund", "nav", "amc", "redemption"]):
                content = json.dumps({
                    "entity_type": "mutual_fund",
                    "regulator": "SEBI",
                    "product": "mutual_fund_units",
                    "reasoning": "Mutual fund servicing is governed under SEBI MF Regulations."
                })
            elif any(w in target_text for w in ["broker", "demat", "shares", "trading"]):
                content = json.dumps({
                    "entity_type": "broker",
                    "regulator": "SEBI",
                    "product": "equity_broking",
                    "reasoning": "Stock brokers are registered SEBI intermediaries."
                })
            else:
                content = json.dumps({
                    "entity_type": "other",
                    "regulator": "UNDETERMINED",
                    "product": "general",
                    "reasoning": "Unrecognized financial product."
                })

        elif "plan" in (step or ""):
            if "bank" in target_text or "fixed deposit" in target_text:
                content = json.dumps({
                    "action": "search_corpus",
                    "args": {"query": "bank fixed deposit interest grievance deficiency in service", "framework": "RBI", "k": 3}
                })
            else:
                content = json.dumps({
                    "action": "search_corpus",
                    "args": {"query": "investor grievance redressal timeline broker", "framework": "SEBI", "k": 3}
                })

        elif "reflect" in (step or ""):
            if "bank" in target_text or "fixed deposit" in target_text:
                content = json.dumps({
                    "reflection": "Product identified as bank fixed deposit. Jurisdiction redirect from SEBI to RBI applies.",
                    "requires_further_search": True,
                    "framework_swap": True,
                    "next_framework": "RBI",
                    "next_query": "bank fixed deposit interest grievance deficiency in service"
                })
            else:
                content = json.dumps({
                    "reflection": "Retrieved evidence directly addresses servicing requirements.",
                    "requires_further_search": False,
                    "framework_swap": False,
                    "next_framework": None,
                    "next_query": None
                })

        elif "draft" in (step or ""):
            if "fixed deposit" in target_text or "bank" in target_text:
                content = json.dumps({
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
                content = json.dumps({
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

        elif "verify" in (step or ""):
            content = json.dumps({
                "decision": "accept",
                "passed": True,
                "unsupported_claims": [],
                "suggested_strategy": None
            })

        return {
            "id": "mock-local",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost_usd": 0.0},
            "model": "heuristic-mock",
            "degraded": True
        }

    @staticmethod
    def extract_json(text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        return {}
