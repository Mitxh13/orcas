from typing import Any, Dict, List, Optional, TypedDict


class Budget(TypedDict):
    steps: int
    max_steps: int
    tool_calls: int
    max_tool_calls: int
    started_at: float
    max_wall_clock_s: float


class PassageItem(TypedDict, total=False):
    chunk_id: str
    doc_id: str
    doc_title: str
    circular_ref: str
    clause: str
    framework: str
    entity_type: str
    heading_path: str
    text: str
    query_used: str
    step: int


class AgentState(TypedDict, total=False):
    run_id: str
    complaint_fields: Dict[str, Any]
    normalized_text: str
    classification: Dict[str, Any]
    evidence_ledger: List[PassageItem]
    queries_tried: List[str]
    current_framework: str
    pending_tool_call: Optional[Dict[str, Any]]
    reflection: Optional[Dict[str, Any]]
    strategy_idx: int
    budget: Budget
    draft: Optional[Dict[str, Any]]
    verification_outcome: Optional[Dict[str, Any]]
    verify_attempts: int
    status: str
    escalation: Optional[Dict[str, Any]]
    prompt_versions: Dict[str, str]
    degraded: bool
