"""
LangGraph Researcher Agent for ORCAS (M5, M8, M12, M13 updates).

Changes vs previous version
-----------------------------
* ``MAX_STEPS``, ``MAX_TOOL_CALLS`` and ``RUN_WALL_CLOCK_S`` are read from
  the environment; hard-coded defaults only used as fallbacks.
* Hard-coded bank-swap branch in ``node_reflect`` deleted.  The LLM decides
  ``framework_swap`` and ``next_query`` from what it read.
* Initial ``current_framework`` is now empty string, not ``"SEBI"``.
* Escalations report ``regulator: "UNDETERMINED"`` unless a single regulator
  has been established.
* Prompt versions are configurable via env (M13):
  ``PROMPT_RESEARCHER_CLASSIFY``, ``PROMPT_RESEARCHER_REFLECT``,
  ``PROMPT_RESEARCHER_DRAFT``.
* Wall-clock check is performed before every LLM/tool call (not only in plan).
* Loop stops when proposed query was already tried or when last search added
  no new chunk IDs.
* ``degraded`` flag in state: if any LLM response carries ``degraded=True``
  it is copied to the determination.
* Evidence ledger is surfaced on the draft as ``_evidence_ledger`` so that
  ``tasks.py`` can pass it to the verifier (ARCHITECTURE §4.1 hand-off).
* Classify now extracts ``stated_regulator`` and sets ``current_framework``
  accordingly.
* Reflect and reflect event emitted for trace (M12).
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph, START, END

from services.worker.agent.state import AgentState, PassageItem, Budget
from services.worker.agent.tools import AgentToolExecutor
from services.worker.clients.fake_retrieval import FakeRetrievalClient
from services.worker.clients.llm_client import LLMClient
from prompts.loader import prompt_loader

logger = logging.getLogger("worker.researcher")

# ---------------------------------------------------------------------------
# Environment-driven loop bounds (M5)
# ---------------------------------------------------------------------------
_MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))
_MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS", "8"))
_RUN_WALL_CLOCK_S = float(os.getenv("RUN_WALL_CLOCK_S", "45.0"))

# Prompt versions from env (M13)
_V_CLASSIFY = os.getenv("PROMPT_RESEARCHER_CLASSIFY", "v1")
_V_REFLECT = os.getenv("PROMPT_RESEARCHER_REFLECT", "v2")
_V_DRAFT = os.getenv("PROMPT_RESEARCHER_DRAFT", "v1")

# Verifier retry strategies, in rotation order (ARCHITECTURE §4.2). One is never repeated in a run.
RETRY_STRATEGIES = ("query_rewrite", "framework_swap", "clause_follow")
_CROSS_REFERENCE = re.compile(
    r"\b(?:clause|paragraph|para|regulation|section)\s+(\d+[A-Z]?)\b", re.IGNORECASE)

ALLOWED_ENTITIES = {
    "broker", "mutual_fund", "registrar", "listed_company",
    "depository", "bank", "other", "unknown",
}
SEBI_ENTITIES = {
    "broker", "mutual_fund", "registrar", "listed_company", "depository",
}


class ResearcherGraph:
    """
    LangGraph implementation of the ORCAS Researcher Agent.
    Orchestrates bounded multi-step research, modifier hunting, and draft
    determination synthesis.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        retrieval_client: Optional[FakeRetrievalClient] = None,
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
                "draft": "draft",
            },
        )

        builder.add_edge("research_tool", "research_reflect")

        builder.add_conditional_edges(
            "research_reflect",
            self._route_after_reflect,
            {
                "plan": "research_plan",
                "draft": "draft",
                "escalate": "escalate",
            },
        )

        builder.add_edge("draft", END)
        builder.add_edge("escalate", END)

        return builder.compile()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _wall_clock_exceeded(self, budget: Dict[str, Any]) -> bool:
        return (time.time() - budget["started_at"]) > budget["max_wall_clock_s"]

    def _effective_regulator(self, state: AgentState) -> str:
        """Return the established regulator, or UNDETERMINED."""
        fw = state.get("current_framework", "")
        return fw if fw in ("SEBI", "RBI") else "UNDETERMINED"

    # -----------------------------------------------------------------------
    # Nodes
    # -----------------------------------------------------------------------

    def node_normalize(self, state: AgentState) -> Dict[str, Any]:
        text = state.get("complaint_fields", {}).get("text", "").strip()
        cleaned = " ".join(text.split())
        return {"normalized_text": cleaned, "status": "running"}

    def node_classify(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]
        if self._wall_clock_exceeded(budget):
            return {
                "escalation": {
                    "reason_code": "budget_exhausted",
                    "reason": "Wall-clock limit exceeded before classification.",
                },
                "budget": budget,
            }

        text = state["normalized_text"]
        prompt = prompt_loader.render("researcher_classify", _V_CLASSIFY, complaint_text=text)

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="classify",
            prompt_version=_V_CLASSIFY,
        )
        is_degraded = response.get("degraded", False)
        content = response["choices"][0]["message"]["content"]
        classification = self.llm.extract_json(content)

        stated_regulator = classification.get("stated_regulator")
        raw_regulator = classification.get("regulator", "")

        # Start research in stated_regulator if present, else use
        # the classification result, else empty (UNDETERMINED)
        if stated_regulator in ("SEBI", "RBI"):
            start_framework = stated_regulator
        elif raw_regulator in ("SEBI", "RBI"):
            start_framework = raw_regulator
        else:
            start_framework = ""  # will show as UNDETERMINED

        return {
            "classification": classification,
            "current_framework": start_framework,
            "degraded": is_degraded,
            "prompt_versions": {"researcher_classify": _V_CLASSIFY},
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
                    "reason": (
                        f"Execution bounds exceeded: steps={budget['steps']}/{budget['max_steps']}, "
                        f"elapsed={elapsed:.1f}s/{budget['max_wall_clock_s']}s"
                    ),
                },
            }

        # Out-of-corpus / split-regulator check
        entity_type = state.get("classification", {}).get("entity_type", "")
        regulators_involved = state.get("classification", {}).get("regulators_involved", [])
        raw_regulator = state.get("classification", {}).get("regulator", "")

        if len(regulators_involved) >= 2:
            return {
                "budget": budget,
                "escalation": {
                    "reason_code": "split_regulators",
                    "reason": f"Complaint involves multiple regulators: {regulators_involved}.",
                },
            }

        if entity_type in ("other", "unknown") or raw_regulator == "UNDETERMINED":
            if entity_type not in ALLOWED_ENTITIES - {"other", "unknown"}:
                return {
                    "budget": budget,
                    "escalation": {
                        "reason_code": "out_of_corpus",
                        "reason": (
                            f"Entity type '{entity_type}' is outside the covered regulatory corpus."
                        ),
                    },
                }

        # Determine next query and framework from reflection
        reflection = state.get("reflection") or {}
        if reflection.get("next_query"):
            query = reflection["next_query"]
            framework = reflection.get("next_framework") or state.get("current_framework") or "SEBI"
        else:
            product = state.get("classification", {}).get("product", "grievance")
            query = f"{product} grievance redressal"
            framework = state.get("current_framework") or "SEBI"

        # Stop if query already tried
        queries_tried = state.get("queries_tried", [])
        if query in queries_tried:
            # Choose draft instead of re-running the same query
            return {"budget": budget, "pending_tool_call": None}

        pending_tool = {
            "tool_name": "search_corpus",
            "args": {"query": query, "framework": framework, "k": 5},
        }
        return {"budget": budget, "pending_tool_call": pending_tool}

    def node_tool(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]
        budget["tool_calls"] += 1

        # Wall-clock check before every tool call (M5)
        if self._wall_clock_exceeded(budget):
            return {
                "budget": budget,
                "pending_tool_call": None,
                "escalation": {
                    "reason_code": "budget_exhausted",
                    "reason": "Wall-clock limit exceeded during tool execution.",
                },
            }

        tool_call = state.get("pending_tool_call", {})
        tool_name = tool_call.get("tool_name", "search_corpus")
        args = tool_call.get("args", {})

        executor = AgentToolExecutor(self.retrieval, state.get("complaint_fields", {}))
        passages = self._as_passages(executor.execute(tool_name, args))

        ledger = list(state.get("evidence_ledger", []))
        prev_chunk_ids = {p.get("chunk_id") for p in ledger}
        new_chunk_ids = set()

        for p in passages:
            cid = p.get("chunk_id")
            if cid not in prev_chunk_ids:
                entry = dict(p)
                entry["query_used"] = args.get("query", "")
                entry["step"] = budget["steps"]
                ledger.append(entry)
                new_chunk_ids.add(cid)

        queries = list(state.get("queries_tried", []))
        if args.get("query"):
            queries.append(args["query"])

        return {
            "budget": budget,
            "evidence_ledger": ledger,
            "queries_tried": queries,
            "pending_tool_call": None,
            "last_search_new_chunks": len(new_chunk_ids),
        }

    def node_reflect(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]

        # Wall-clock check before LLM call (M5)
        if self._wall_clock_exceeded(budget):
            return {
                "budget": budget,
                "escalation": {
                    "reason_code": "budget_exhausted",
                    "reason": "Wall-clock limit exceeded before reflection.",
                },
            }

        text = state["normalized_text"]
        classification = state.get("classification", {})
        ledger = state.get("evidence_ledger", [])

        # Provide ~400 chars per passage + scope card for current framework (M5)
        ledger_summary = json.dumps([
            {
                "chunk_id": p.get("chunk_id"),
                "framework": p.get("framework"),
                "clause": p.get("clause"),
                "text": p.get("text", "")[:400],
            }
            for p in ledger
        ])

        prompt = prompt_loader.render(
            "researcher_reflect",
            _V_REFLECT,
            complaint_text=text,
            classification=json.dumps(classification),
            evidence_ledger=ledger_summary,
        )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="reflect",
            prompt_version=_V_REFLECT,
        )
        is_degraded = response.get("degraded", False) or state.get("degraded", False)
        content = response["choices"][0]["message"]["content"]
        reflection = self.llm.extract_json(content)

        # Framework swap: use what the LLM decided; no hard-coded branch
        if reflection.get("framework_swap") and reflection.get("next_framework") in ("SEBI", "RBI"):
            new_framework = reflection["next_framework"]
            logger.info(
                "Framework swap: %s → %s (query: %s)",
                state.get("current_framework"),
                new_framework,
                reflection.get("next_query"),
            )
            return {
                "reflection": reflection,
                "current_framework": new_framework,
                "degraded": is_degraded,
            }

        return {"reflection": reflection, "degraded": is_degraded}

    def node_draft(self, state: AgentState) -> Dict[str, Any]:
        budget = state["budget"]

        # Wall-clock check
        if self._wall_clock_exceeded(budget):
            # The draft node leads straight to END, so build the escalation here; returning
            # only an `escalation` key would end the graph with no determination at all.
            return self.node_escalate({**state, "escalation": {
                "reason_code": "budget_exhausted",
                "reason": "Wall-clock limit exceeded before drafting.",
            }})

        text = state["normalized_text"]
        classification = state.get("classification", {})
        ledger = state.get("evidence_ledger", [])

        prompt = prompt_loader.render(
            "researcher_draft",
            _V_DRAFT,
            complaint_text=text,
            classification=json.dumps(classification),
            evidence_ledger=json.dumps(ledger),
        )
        if state.get("retry_feedback"):
            prompt += (
                "\n\nVerifier feedback on the previous draft: " + str(state["retry_feedback"])
                + "\nCorrect this in the new draft. Cite only passages in the evidence ledger "
                "and quote them exactly."
            )

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            agent="researcher",
            step="draft",
            prompt_version=_V_DRAFT,
        )
        is_degraded = response.get("degraded", False) or state.get("degraded", False)
        content = response["choices"][0]["message"]["content"]
        draft_json = self.llm.extract_json(content)

        raw_entity = draft_json.get("entity_type", classification.get("entity_type", "unknown"))
        safe_entity = raw_entity if raw_entity in ALLOWED_ENTITIES else "other"

        # If the model returned escalate_to_human, produce escalation status
        route = draft_json.get("route", "assign_to_entity")
        if route == "escalate_to_human":
            determination = {
                "run_id": state.get("run_id"),
                "status": "escalated",
                "route": "escalate_to_human",
                "regulator": draft_json.get("regulator", self._effective_regulator(state)),
                "entity_type": safe_entity,
                "timeline": None,
                "basis": draft_json.get("basis", "Agent escalated due to low confidence."),
                "citations": [],
                "escalation": {
                    "reason_code": "low_confidence",
                    "reason": draft_json.get("basis", "Model escalated to human."),
                    "partial_findings": ledger,
                },
                "verification": {
                    "passed": False,
                    "claims_checked": 0,
                    "claims_supported": 0,
                    "attempts": state.get("verify_attempts", 1),
                },
                "prompt_versions": {
                    "researcher_classify": _V_CLASSIFY,
                    "researcher_reflect": _V_REFLECT,
                    "researcher_draft": _V_DRAFT,
                },
                "index_version": getattr(self.retrieval, "index_version", "v1.0-fixtures"),
                "degraded": is_degraded,
                "disposed": False,
                "_evidence_ledger": ledger,
            }
        else:
            determination = {
                "run_id": state.get("run_id"),
                "status": "completed",
                "route": route,
                "regulator": draft_json.get("regulator", self._effective_regulator(state)),
                "entity_type": safe_entity,
                "timeline": draft_json.get("timeline"),
                "basis": draft_json.get("basis", "Drafted based on retrieved regulatory evidence."),
                "citations": draft_json.get("citations", []),
                "escalation": None,
                "verification": {
                    "passed": True,
                    "claims_checked": len(draft_json.get("citations", [])) or 1,
                    "claims_supported": len(draft_json.get("citations", [])) or 1,
                    "attempts": state.get("verify_attempts", 1),
                },
                "prompt_versions": {
                    "researcher_classify": _V_CLASSIFY,
                    "researcher_reflect": _V_REFLECT,
                    "researcher_draft": _V_DRAFT,
                },
                "index_version": getattr(self.retrieval, "index_version", "v1.0-fixtures"),
                "degraded": is_degraded,
                "disposed": False,
                "_evidence_ledger": ledger,
            }

        return {"draft": determination, "status": determination["status"]}

    def node_escalate(self, state: AgentState) -> Dict[str, Any]:
        esc_info = state.get("escalation") or {
            "reason_code": "budget_exhausted",
            "reason": "Agent execution halted by loop bounds.",
        }
        raw_entity = state.get("classification", {}).get("entity_type", "unknown")
        safe_entity = raw_entity if raw_entity in ALLOWED_ENTITIES else "other"

        determination = {
            "run_id": state.get("run_id"),
            "status": "escalated",
            "route": "escalate_to_human",
            "regulator": self._effective_regulator(state),  # UNDETERMINED unless established
            "entity_type": safe_entity,
            "timeline": None,
            "basis": f"Escalated to human grievance officer: {esc_info.get('reason')}",
            "citations": [],
            "escalation": {
                "reason_code": esc_info.get("reason_code", "low_confidence"),
                "reason": esc_info.get("reason", "Escalated by rule."),
                "partial_findings": state.get("evidence_ledger", []),
            },
            "verification": {
                "passed": False,
                "claims_checked": 0,
                "claims_supported": 0,
                "attempts": state.get("verify_attempts", 1),
            },
            "prompt_versions": {
                "researcher_classify": _V_CLASSIFY,
                "researcher_reflect": _V_REFLECT,
            },
            "index_version": getattr(self.retrieval, "index_version", "v1.0-fixtures"),
            "degraded": state.get("degraded", False),
            "disposed": False,
            "_evidence_ledger": state.get("evidence_ledger", []),
        }
        return {"draft": determination, "status": "escalated"}

    # -----------------------------------------------------------------------
    # Routing
    # -----------------------------------------------------------------------

    def _route_after_plan(self, state: AgentState) -> str:
        if state.get("escalation"):
            return "escalate"
        pending = state.get("pending_tool_call")
        if not pending:
            return "draft"
        return "tool"

    def _route_after_reflect(self, state: AgentState) -> str:
        if state.get("escalation"):
            return "escalate"

        reflection = state.get("reflection") or {}
        budget = state.get("budget", {})

        # Stop if no further search needed
        if not reflection.get("requires_further_search"):
            return "draft"

        # Stop if tool budget exhausted
        if budget.get("tool_calls", 0) >= budget.get("max_tool_calls", _MAX_TOOL_CALLS):
            return "draft"

        # Stop if last search added no new chunks (M5 — idle loop guard)
        if state.get("last_search_new_chunks", 1) == 0:
            return "draft"

        return "plan"

    # -----------------------------------------------------------------------
    # Run entrypoint
    # -----------------------------------------------------------------------

    def run(self, run_id: str, complaint_fields: Dict[str, Any]) -> Dict[str, Any]:
        initial_state: AgentState = {
            "run_id": run_id,
            "complaint_fields": complaint_fields,
            "normalized_text": "",
            "classification": {},
            "evidence_ledger": [],
            "queries_tried": [],
            "current_framework": "",  # M8: empty, not "SEBI"
            "pending_tool_call": None,
            "reflection": None,
            "budget": {
                "steps": 0,
                "max_steps": _MAX_STEPS,
                "tool_calls": 0,
                "max_tool_calls": _MAX_TOOL_CALLS,
                "started_at": time.time(),
                "max_wall_clock_s": _RUN_WALL_CLOCK_S,
            },
            "draft": None,
            "verification_outcome": None,
            "verify_attempts": 1,
            "status": "queued",
            "escalation": None,
            "prompt_versions": {},
            "degraded": False,
            "last_search_new_chunks": 0,
            "retry_feedback": None,
        }
        final_state = self.graph.invoke(initial_state)
        return self._with_research_state(final_state.get("draft"), final_state, [])

    # -----------------------------------------------------------------------
    # Verifier retry (M6): search again with a different strategy, then re-draft
    # -----------------------------------------------------------------------

    def retry(self, run_id: str, complaint_fields: Dict[str, Any], prior_draft: Dict[str, Any],
              suggested_strategy: Optional[str], reason: str) -> Dict[str, Any]:
        """Re-research and re-draft after the verifier rejected ``prior_draft``.

        The run's ledger, queries and budget carry over, so the bounds still cover the whole run.
        The returned draft records the strategy used under ``_research_state``.
        """
        prior = prior_draft.get("_research_state") or {}
        used = list(prior.get("strategies_used", []))
        state: AgentState = {
            "run_id": run_id,
            "complaint_fields": complaint_fields,
            "normalized_text": " ".join(complaint_fields.get("text", "").split()),
            "classification": prior.get("classification", {}),
            "evidence_ledger": list(prior_draft.get("_evidence_ledger", [])),
            "queries_tried": list(prior.get("queries_tried", [])),
            "current_framework": prior.get("current_framework", ""),
            "budget": prior.get("budget") or {
                "steps": 0, "max_steps": _MAX_STEPS, "tool_calls": 0,
                "max_tool_calls": _MAX_TOOL_CALLS, "started_at": time.time(),
                "max_wall_clock_s": _RUN_WALL_CLOCK_S,
            },
            "verify_attempts": len(used) + 2,
            "degraded": bool(prior_draft.get("degraded", False)),
            "retry_feedback": reason,
        }

        strategy, tool_call = self._retry_tool_call(state, prior_draft, suggested_strategy, used)
        used.append(strategy)
        budget = state["budget"]
        if tool_call and budget["tool_calls"] < budget["max_tool_calls"]:
            state["pending_tool_call"] = tool_call
            if tool_call["args"].get("framework"):
                state["current_framework"] = tool_call["args"]["framework"]
            state.update(self.node_tool(state))

        if state.get("escalation"):
            state.update(self.node_escalate(state))
        else:
            state.update(self.node_draft(state))
        return self._with_research_state(state.get("draft"), state, used)

    def _retry_tool_call(self, state: AgentState, prior_draft: Dict[str, Any],
                         suggested: Optional[str], used: List[str]):
        """Pick the first unused strategy, starting from the verifier's suggestion, that can act."""
        order = [s for s in ([suggested] if suggested in RETRY_STRATEGIES else []) +
                 list(RETRY_STRATEGIES) if s not in used]
        order = list(dict.fromkeys(order)) or ["query_rewrite"]
        for strategy in order:
            tool_call = getattr(self, f"_strategy_{strategy}")(state, prior_draft)
            if tool_call:
                return strategy, tool_call
        return order[0], None

    def _strategy_query_rewrite(self, state: AgentState, prior_draft: Dict[str, Any]):
        """New query built from the headings of passages already read plus the complaint."""
        framework = state.get("current_framework") or None
        headings: List[str] = []
        for passage in state.get("evidence_ledger", []):
            if framework and passage.get("framework") != framework:
                continue
            path = passage.get("heading_path") or []
            for heading in ([path] if isinstance(path, str) else path)[-1:]:
                if heading and heading not in headings:
                    headings.append(heading)
        query = " ".join(headings[:2] + [state["normalized_text"][:160]]).strip()
        if not query or query in state.get("queries_tried", []):
            return None
        return {"tool_name": "search_corpus", "args": {"query": query, "framework": framework, "k": 5}}

    def _strategy_framework_swap(self, state: AgentState, prior_draft: Dict[str, Any]):
        """Search the other framework; a draft may be citing the wrong regulator's text."""
        current = state.get("current_framework")
        searched = {p.get("framework") for p in state.get("evidence_ledger", [])}
        target = prior_draft.get("regulator")
        if target not in ("SEBI", "RBI") or target in searched:
            target = "RBI" if current == "SEBI" else "SEBI"
        if target in searched:
            return None  # both frameworks already have evidence in the ledger
        queries = state.get("queries_tried", [])
        product = state.get("classification", {}).get("product", "grievance")
        query = queries[-1] if queries else f"{product} grievance redressal"
        return {"tool_name": "search_corpus", "args": {"query": query, "framework": target, "k": 5}}

    def _strategy_clause_follow(self, state: AgentState, prior_draft: Dict[str, Any]):
        """Fetch a clause that a passage already read refers to and that is not yet in the ledger."""
        ledger = state.get("evidence_ledger", [])
        have = {(p.get("doc_id"), str(p.get("clause_id") or p.get("clause") or "")) for p in ledger}
        for passage in ledger:
            for clause_id in _CROSS_REFERENCE.findall(passage.get("text", "")):
                if passage.get("doc_id") and (passage["doc_id"], clause_id) not in have:
                    return {"tool_name": "get_clause",
                            "args": {"doc_id": passage["doc_id"], "clause_id": clause_id}}
        return None

    def _as_passages(self, result: Any) -> List[Dict[str, Any]]:
        """Tool results as ledger passages: a search returns a list, get_clause a single clause."""
        if not result:
            return []
        if isinstance(result, list):
            return result
        if result.get("chunk_id"):
            return [result]
        chunks = [self.retrieval.get_chunk(chunk_id) for chunk_id in result.get("chunk_ids", [])]
        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _with_research_state(draft: Optional[Dict[str, Any]], state: AgentState,
                             strategies_used: List[str]) -> Optional[Dict[str, Any]]:
        """Attach what a retry needs to continue this run; underscore keys are never stored."""
        if not draft:
            return draft
        draft = dict(draft)
        draft["_research_state"] = {
            "classification": state.get("classification", {}),
            "queries_tried": list(state.get("queries_tried", [])),
            "current_framework": state.get("current_framework", ""),
            "budget": state.get("budget"),
            "strategies_used": list(strategies_used),
        }
        return draft
