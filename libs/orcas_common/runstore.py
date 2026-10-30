"""Run persistence used by gateway and Celery workers.

The in-memory store is for isolated tests and the fake-worker smoke path. A real
deployment uses Postgres because gateway and workers run in separate processes.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import database_url

TERMINAL = frozenset({"completed", "escalated", "failed"})


class RunStore(Protocol):
    def create_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def claim_run(self, run_id: str) -> bool: ...
    def update_status(self, run_id: str, status: str) -> None: ...
    def complete(self, run_id: str, determination: dict[str, Any]) -> None: ...
    def fail(self, run_id: str, error_message: str) -> None: ...
    def append_event(self, run_id: str, agent: str, step: str, kind: str,
                     latency_ms: int = 0, payload: dict[str, Any] | None = None,
                     tokens: dict[str, int] | None = None, cost_usd: float = 0.0) -> None: ...
    def get_events(self, run_id: str) -> list[dict[str, Any]]: ...
    def quarantine(self, raw_payload: Any, reason: str,
                   errors: list[dict[str, Any]]) -> str: ...
    def get_quarantine(self, quarantine_id: str) -> dict[str, Any] | None: ...
    def list_quarantine(self, limit: int = 100) -> list[dict[str, Any]]: ...
    def add_feedback(self, run_id: str, rating: str, reason: str) -> dict[str, Any]: ...
    def feedback_summary(self) -> dict[str, int]: ...
    def add_eval_score(self, run_id: str, metric: str, score: float,
                       details: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def ping(self) -> bool: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.quarantined: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []
        self.eval_scores: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def create_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if run_id not in self.runs:
                self.runs[run_id] = {
                    "run_id": run_id,
                    "status": "queued",
                    "payload": payload,
                    "submitted_at": _now(),
                    "started_at": None,
                    "finished_at": None,
                    "result": None,
                    "error": None,
                }
                self.events[run_id] = []
            return dict(self.runs[run_id])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self.runs.get(run_id)
            return dict(record) if record else None

    def update_status(self, run_id: str, status: str) -> None:
        with self._lock:
            record = self.runs[run_id]
            if record["status"] in TERMINAL:
                return
            record["status"] = status
            if status == "running" and record["started_at"] is None:
                record["started_at"] = _now()

    def claim_run(self, run_id: str) -> bool:
        with self._lock:
            record = self.runs[run_id]
            if record["status"] != "queued":
                return False
            record["status"] = "running"
            record["started_at"] = _now()
            return True

    def complete(self, run_id: str, determination: dict[str, Any]) -> None:
        status = determination.get("status", "completed")
        if status not in TERMINAL:
            raise ValueError("determination must have a terminal status")
        with self._lock:
            record = self.runs[run_id]
            if record["status"] in TERMINAL:
                return
            record.update(status=status, result=determination, finished_at=_now())

    def fail(self, run_id: str, error_message: str) -> None:
        with self._lock:
            record = self.runs[run_id]
            if record["status"] in TERMINAL:
                return
            record.update(status="failed", error=error_message, finished_at=_now())

    def append_event(
        self, run_id: str, agent: str, step: str, kind: str,
        latency_ms: int = 0, payload: dict[str, Any] | None = None,
        tokens: dict[str, int] | None = None, cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            events = self.events[run_id]
            events.append({
                "run_id": run_id, "seq": len(events) + 1, "ts": _now(),
                "agent": agent, "step": step, "kind": kind,
                "latency_ms": latency_ms, "payload": payload or {},
                "tokens": tokens or {}, "cost_usd": cost_usd,
            })

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self.events.get(run_id, [])]

    def quarantine(self, raw_payload: Any, reason: str, errors: list[dict[str, Any]]) -> str:
        quarantine_id = str(uuid.uuid4())
        with self._lock:
            self.quarantined.append({
                "quarantine_id": quarantine_id, "received_at": _now(),
                "raw_payload": raw_payload, "reason": reason, "errors": errors,
            })
        return quarantine_id

    def get_quarantine(self, quarantine_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(item) for item in self.quarantined
                         if item["quarantine_id"] == quarantine_id), None)

    def list_quarantine(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in reversed(self.quarantined[-limit:])]

    def add_feedback(self, run_id: str, rating: str, reason: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            record = {"feedback_id": str(uuid.uuid4()), "run_id": run_id,
                      "rating": rating, "reason": reason, "created_at": _now()}
            self.feedback.append(record)
            return dict(record)

    def feedback_summary(self) -> dict[str, int]:
        with self._lock:
            up = sum(item["rating"] == "up" for item in self.feedback)
            down = sum(item["rating"] == "down" for item in self.feedback)
        return {"up": up, "down": down, "total": up + down}

    def add_eval_score(self, run_id: str, metric: str, score: float,
                       details: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            record = {"score_id": str(uuid.uuid4()), "run_id": run_id,
                      "metric": metric, "score": score, "details": details or {},
                      "measured_at": _now()}
            self.eval_scores.append(record)
            return dict(record)

    def ping(self) -> bool:
        return True


class PostgresRunStore:
    def __init__(self, url: str | None = None) -> None:
        self.url = url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.url or database_url(), row_factory=dict_row)

    @staticmethod
    def _record(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("run_id", "quarantine_id", "feedback_id", "score_id"):
            if key in result:
                result[key] = str(result[key])
        for key in ("submitted_at", "started_at", "finished_at", "received_at", "created_at", "measured_at", "ts"):
            if result.get(key) is not None:
                result[key] = result[key].isoformat()
        return result

    def create_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs (run_id, status, payload) VALUES (%s, 'queued', %s) "
                "ON CONFLICT (run_id) DO NOTHING", (run_id, Jsonb(payload)),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,)).fetchone()
        return self._record(row)

    def update_status(self, run_id: str, status: str) -> None:
        if status not in {"queued", "running"}:
            raise ValueError("update_status only accepts nonterminal states")
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = %s, started_at = CASE WHEN %s = 'running' "
                "THEN COALESCE(started_at, NOW()) ELSE started_at END "
                "WHERE run_id = %s AND status NOT IN ('completed', 'escalated', 'failed')",
                (status, status, run_id),
            )

    def claim_run(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "UPDATE runs SET status = 'running', started_at = NOW() WHERE run_id = %s "
                "AND (status = 'queued' OR (status = 'running' "
                "AND (started_at IS NULL OR started_at < NOW() - INTERVAL '120 seconds'))) "
                "RETURNING run_id", (run_id,),
            ).fetchone()
        return row is not None

    def complete(self, run_id: str, determination: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        status = determination.get("status", "completed")
        if status not in TERMINAL:
            raise ValueError("determination must have a terminal status")
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = %s, result = %s, finished_at = NOW() "
                "WHERE run_id = %s AND status NOT IN ('completed', 'escalated', 'failed')",
                (status, Jsonb(determination), run_id),
            )

    def fail(self, run_id: str, error_message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = 'failed', error = %s, finished_at = NOW() "
                "WHERE run_id = %s AND status NOT IN ('completed', 'escalated', 'failed')",
                (error_message, run_id),
            )

    def append_event(
        self, run_id: str, agent: str, step: str, kind: str,
        latency_ms: int = 0, payload: dict[str, Any] | None = None,
        tokens: dict[str, int] | None = None, cost_usd: float = 0.0,
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as connection:
            # The run lock serializes sequence allocation across workers.
            connection.execute("SELECT run_id FROM runs WHERE run_id = %s FOR UPDATE", (run_id,))
            connection.execute(
                "INSERT INTO run_events (run_id, seq, agent, step, kind, latency_ms, payload, tokens, cost_usd) "
                "VALUES (%s, (SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = %s), "
                "%s, %s, %s, %s, %s, %s, %s)",
                (run_id, run_id, agent, step, kind, latency_ms,
                 Jsonb(payload or {}), Jsonb(tokens or {}), cost_usd),
            )

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = %s ORDER BY seq", (run_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def quarantine(self, raw_payload: Any, reason: str, errors: list[dict[str, Any]]) -> str:
        from psycopg.types.json import Jsonb

        quarantine_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO quarantine (quarantine_id, raw_payload, reason, errors) "
                "VALUES (%s, %s, %s, %s)",
                (quarantine_id, Jsonb(raw_payload), reason, Jsonb(errors)),
            )
        return quarantine_id

    def get_quarantine(self, quarantine_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM quarantine WHERE quarantine_id = %s", (quarantine_id,),
            ).fetchone()
        return self._record(row)

    def list_quarantine(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM quarantine ORDER BY received_at DESC LIMIT %s", (limit,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def add_feedback(self, run_id: str, rating: str, reason: str) -> dict[str, Any]:
        feedback_id = str(uuid.uuid4())
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO feedback (feedback_id, run_id, rating, reason) "
                "VALUES (%s, %s, %s, %s) RETURNING *",
                (feedback_id, run_id, rating, reason),
            ).fetchone()
        return self._record(row)

    def feedback_summary(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE rating = 'up') AS up, "
                "COUNT(*) FILTER (WHERE rating = 'down') AS down, "
                "COUNT(*) AS total FROM feedback"
            ).fetchone()
        return dict(row)

    def add_eval_score(self, run_id: str, metric: str, score: float,
                       details: dict[str, Any] | None = None) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        score_id = str(uuid.uuid4())
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO eval_scores (score_id, run_id, metric, score, details) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (score_id, run_id, metric, score, Jsonb(details or {})),
            ).fetchone()
        return self._record(row)

    def ping(self) -> bool:
        with self._connect() as connection:
            connection.execute("SELECT 1")
        return True
