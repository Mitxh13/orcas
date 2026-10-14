import time
from typing import Any, Dict, List, Optional


class InMemoryRunStore:
    """
    Dict-backed RunStore implementation.
    Enables worker development and testing before PostgreSQL is connected.
    """
    def __init__(self):
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.events: Dict[str, List[Dict[str, Any]]] = {}

    def create_run(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "run_id": run_id,
            "status": "queued",
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": payload,
            "result": None,
            "degraded": False
        }
        self.runs[run_id] = record
        self.events[run_id] = []
        return record

    def update_status(self, run_id: str, status: str) -> None:
        if run_id in self.runs:
            self.runs[run_id]["status"] = status
            if status == "running" and "started_at" not in self.runs[run_id]:
                self.runs[run_id]["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def complete(self, run_id: str, determination: Dict[str, Any]) -> None:
        if run_id in self.runs:
            self.runs[run_id]["status"] = determination.get("status", "completed")
            self.runs[run_id]["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.runs[run_id]["result"] = determination

    def fail(self, run_id: str, error_message: str) -> None:
        if run_id in self.runs:
            self.runs[run_id]["status"] = "failed"
            self.runs[run_id]["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.runs[run_id]["error"] = error_message

    def append_event(
        self,
        run_id: str,
        agent: str,
        step: str,
        kind: str,
        latency_ms: int = 0,
        payload: Optional[Dict[str, Any]] = None,
        tokens: Optional[Dict[str, int]] = None,
        cost_usd: float = 0.0
    ) -> None:
        if run_id not in self.events:
            self.events[run_id] = []
        seq = len(self.events[run_id]) + 1
        event = {
            "run_id": run_id,
            "seq": seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent": agent,
            "step": step,
            "kind": kind,
            "latency_ms": latency_ms,
            "tokens": tokens or {},
            "cost_usd": cost_usd,
            "payload": payload or {}
        }
        self.events[run_id].append(event)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.runs.get(run_id)

    def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        return self.events.get(run_id, [])
