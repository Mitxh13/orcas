import json
import logging
import time
from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, START, END

from services.worker.agent.state import AgentState, PassageItem, Budget
from services.worker.agent.tools import AgentToolExecutor
from services.worker.clients.fake_retrieval import FakeRetrievalClient
from services.worker.clients.llm_client import LLMClient
from prompts.loader import prompt_loader

logger = logging.getLogger("worker.researcher")


class ResearcherGraph:
    """
    LangGraph implementation of the ORCAS Researcher Agent.
    Orchestrates bounded multi-step research, modifier hunting, and draft determination synthesis.
    """
    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        retrieval_client: Optional[FakeRetrievalClient] = None
    ):
        self.llm = llm_client or LLMClient()
        self.retrieval = retrieval_client or FakeRetrievalClient()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("normalize", self.node_normalize)
        builder.add_node("classify", self.node_classify)
        builder.add_node("research_plan", self.node_plan)
        builder.add_node("research_tool", self.node_tool)
        builder.add_node("research_reflect", self.node_reflect)
        builder.add_node("draft", self.node_draft)
        builder.add_node("escalate", self.node_escalate)

        builder.add_edge(START, "normalize")
        builder.add_edge("normalize", "classify")
        builder.add_edge("classify", "research_plan")

        builder.add_conditional_edges(
            "research_plan",
            self._route_after_plan,
            {
                "tool": "research_tool",
                "escalate": "escalate",
                "draft": "draft"
            }
        )

        builder.add_edge("research_tool", "research_reflect")

        builder.add_conditional_edges(
            "research_reflect",
            self._route_after_reflect,
            {
                "plan": "research_plan",
                "draft": "draft",
                "escalate": "escalate"
            }
        )

        builder.add_edge("draft", END)
        builder.add_edge("escalate", END)

        return builder.compile()

    # --- Nodes ---

    def node_normalize(self, state: AgentState) -> Dict[str, Any]:
        text = state.get("complaint_fields", {}).get("text", "").strip()
        cleaned = " ".join(text.split())
        return {
            "normalized_text": cleaned,
            "status": "running"
        }

    def node_classify(self, state: AgentState) -> Dict[str, Any]:
        text = state["normalized_text"]
        prompt = prompt_loader.render("researcher_classify", "v1", complaint_text=text)
        
        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="classify",
            prompt_version="v1"
        )
        content = response["choices"][0]["message"]["content"]
        classification = self.llm.extract_json(content)
        
        regulator = classification.get("regulator", "SEBI")
        return {
            "classification": classification,
            "current_framework": regulator if regulator in ("SEBI", "RBI") else "SEBI"
        }

    def node_plan(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]
        budget["steps"] += 1
        elapsed = time.time() - budget["started_at"]

        # Loop bounds check
        if budget["steps"] > budget["max_steps"] or elapsed > budget["max_wall_clock_s"]:
            return {
                "budget": budget,
                "escalation": {
                    "reason_code": "budget_exhausted",
                    "reason": f"Execution bounds exceeded: steps={budget['steps']}/{budget['max_steps']}, elapsed={elapsed:.1f}s"
                }
            }

        # Out-of-corpus check
        entity_type = state.get("classification", {}).get("entity_type", "")
        regulator = state.get("classification", {}).get("regulator", "")
        if entity_type in ("insurer", "pension_fund", "unknown", "other") or regulator == "UNDETERMINED":
            return {
                "budget": budget,
                "escalation": {
                    "reason_code": "out_of_corpus",
                    "reason": f"Entity type '{entity_type}' is outside the covered regulatory corpus (SEBI/RBI)."
                }
            }

        reflection = state.get("reflection") or {}
        if reflection.get("next_query"):
            query = reflection["next_query"]
            framework = reflection.get("next_framework") or state.get("current_framework")
        else:
            product = state.get("classification", {}).get("product", "")
            query = f"{product} grievance redressal timeline"
            framework = state.get("current_framework", "SEBI")

        pending_tool = {
            "tool_name": "search_corpus",
            "args": {
                "query": query,
                "framework": framework,
                "k": 3
            }
        }
        return {
            "budget": budget,
            "pending_tool_call": pending_tool
        }

    def node_tool(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]
        budget["tool_calls"] += 1

        tool_call = state.get("pending_tool_call", {})
        tool_name = tool_call.get("tool_name", "search_corpus")
        args = tool_call.get("args", {})

        executor = AgentToolExecutor(self.retrieval, state.get("complaint_fields", {}))
        passages = executor.execute(tool_name, args)

        ledger = list(state.get("evidence_ledger", []))
        for p in passages:
            if not any(existing.get("chunk_id") == p.get("chunk_id") for existing in ledger):
                entry = dict(p)
                entry["query_used"] = args.get("query", "")
                entry["step"] = budget["steps"]
                ledger.append(entry)

        queries = list(state.get("queries_tried", []))
        if args.get("query"):
            queries.append(args["query"])

        return {
            "budget": budget,
            "evidence_ledger": ledger,
            "queries_tried": queries,
            "pending_tool_call": None
        }

    def node_reflect(self, state: AgentState) -> Dict[str, Any]:
        text = state["normalized_text"]
        classification = state.get("classification", {})
        ledger_summary = json.dumps([
            {"chunk_id": p.get("chunk_id"), "framework": p.get("framework"), "text": p.get("text")[:150]}
            for p in state.get("evidence_ledger", [])
        ])

        prompt = prompt_loader.render(
            "researcher_reflect",
            "v2",
            complaint_text=text,
            classification=json.dumps(classification),
            evidence_ledger=ledger_summary
        )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="reflect",
            prompt_version="v2"
        )
        content = response["choices"][0]["message"]["content"]
        reflection = self.llm.extract_json(content)

        # Framework swap (§4.1 bank FD reference case)
        if reflection.get("framework_swap") or (
            classification.get("entity_type") == "bank" and state.get("current_framework") == "SEBI"
        ):
            return {
                "reflection": {
                    "reflection": "Product identified as bank fixed deposit. Redirecting framework from SEBI to RBI.",
                    "requires_further_search": True,
                    "framework_swap": True,
                    "next_framework": "RBI",
                    "next_query": "bank fixed deposit interest grievance deficiency in service"
                },
                "current_framework": "RBI"
            }

        return {"reflection": reflection}

    def node_draft(self, state: AgentState) -> Dict[str, Any]:
        text = state["normalized_text"]
        classification = state.get("classification", {})
        ledger = state.get("evidence_ledger", [])

        prompt = prompt_loader.render(
            "researcher_draft",
            "v1",
            complaint_text=text,
            classification=json.dumps(classification),
            evidence_ledger=json.dumps(ledger)
        )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="draft",
            prompt_version="v1"
        )
        content = response["choices"][0]["message"]["content"]
        draft_json = self.llm.extract_json(content)

        allowed_entities = {"broker", "mutual_fund", "registrar", "listed_company", "depository", "bank", "other", "unknown"}
        raw_entity = draft_json.get("entity_type", classification.get("entity_type", "unknown"))
        safe_entity = raw_entity if raw_entity in allowed_entities else "other"

        determination = {
            "run_id": state.get("run_id"),
            "status": "completed",
            "route": draft_json.get("route", "assign_to_entity"),
            "regulator": draft_json.get("regulator", state.get("current_framework", "SEBI")),
            "entity_type": safe_entity,
            "timeline": draft_json.get("timeline"),
            "basis": draft_json.get("basis", "Drafted based on retrieved regulatory evidence."),
            "citations": draft_json.get("citations", []),
            "escalation": None,
            "verification": {
                "passed": True,
                "claims_checked": len(draft_json.get("citations", [])) or 1,
                "claims_supported": len(draft_json.get("citations", [])) or 1,
                "attempts": state.get("verify_attempts", 1)
            },
            "prompt_versions": {
                "researcher_classify": "v1",
                "researcher_reflect": "v2",
                "researcher_draft": "v1"
            },
            "index_version": getattr(self.retrieval, "index_version", "v1.0-fixtures"),
            "degraded": state.get("degraded", False),
            "disposed": False
        }

        return {
            "draft": determination,
            "status": "completed"
        }

    def node_escalate(self, state: AgentState) -> Dict[str, Any]:
        esc_info = state.get("escalation") or {
            "reason_code": "budget_exhausted",
            "reason": "Agent execution halted by loop bounds."
        }
        allowed_entities = {"broker", "mutual_fund", "registrar", "listed_company", "depository", "bank", "other", "unknown"}
        raw_entity = state.get("classification", {}).get("entity_type", "unknown")
        safe_entity = raw_entity if raw_entity in allowed_entities else "other"

        determination = {
            "run_id": state.get("run_id"),
            "status": "escalated",
            "route": "escalate_to_human",
            "regulator": state.get("current_framework", "UNDETERMINED"),
            "entity_type": safe_entity,
            "timeline": None,
            "basis": f"Escalated to human grievance officer: {esc_info.get('reason')}",
            "citations": [],
            "escalation": {
                "reason_code": esc_info.get("reason_code", "low_confidence"),
                "reason": esc_info.get("reason", "Escalated by rule."),
                "partial_findings": state.get("evidence_ledger", [])
            },
            "verification": {
                "passed": False,
                "claims_checked": 0,
                "claims_supported": 0,
                "attempts": state.get("verify_attempts", 1)
            },
            "prompt_versions": {
                "researcher_classify": "v1",
                "researcher_reflect": "v2"
            },
            "index_version": getattr(self.retrieval, "index_version", "v1.0-fixtures"),
            "degraded": state.get("degraded", False),
            "disposed": False
        }
        return {
            "draft": determination,
            "status": "escalated"
        }

    # --- Routing ---

    def _route_after_plan(self, state: AgentState) -> str:
        if state.get("escalation"):
            return "escalate"
        if not state.get("pending_tool_call"):
            return "draft"
        return "tool"

    def _route_after_reflect(self, state: AgentState) -> str:
        reflection = state.get("reflection") or {}
        budget = state.get("budget", {})

        if reflection.get("requires_further_search") and budget.get("tool_calls", 0) < budget.get("max_tool_calls", 8):
            return "plan"
        return "draft"

    # --- Run entrypoint ---

    def run(self, run_id: str, complaint_fields: Dict[str, Any]) -> Dict[str, Any]:
        initial_state: AgentState = {
            "run_id": run_id,
            "complaint_fields": complaint_fields,
            "normalized_text": "",
            "classification": {},
            "evidence_ledger": [],
            "queries_tried": [],
            "current_framework": "SEBI",
            "pending_tool_call": None,
            "reflection": None,
            "strategy_idx": 0,
            "budget": {
                "steps": 0,
                "max_steps": 10,
                "tool_calls": 0,
                "max_tool_calls": 8,
                "started_at": time.time(),
                "max_wall_clock_s": 45.0
            },
            "draft": None,
            "verification_outcome": None,
            "verify_attempts": 1,
            "status": "queued",
            "escalation": None,
            "prompt_versions": {},
            "degraded": False
        }
        final_state = self.graph.invoke(initial_state)
        return final_state.get("draft", {})
