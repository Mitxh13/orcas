"""Persist retrieval calls as ordered run events (M12)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class ObservedRetrievalClient:
    def __init__(self, client: Any, store: Any, run_id: str):
        self.client = client
        self.store = store
        self.run_id = run_id

    @property
    def index_version(self) -> str:
        return getattr(self.client, "index_version", "unknown")

    def search(
        self,
        query: str,
        k: int = 5,
        framework: Optional[str] = None,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        started = time.monotonic()
        results = self.client.search(query, k=k, framework=framework, entity_type=entity_type)
        latency_ms = round((time.monotonic() - started) * 1000)

        if self.store and self.run_id:
            self.store.append_event(
                run_id=self.run_id,
                agent="retrieval",
                step="search",
                kind="tool_call",
                latency_ms=latency_ms,
                payload={
                    "tool": "search_corpus",
                    "query": query,
                    "framework": framework,
                    "entity_type": entity_type,
                    "k": k,
                    "passages": [
                        {
                            "chunk_id": p.get("chunk_id"),
                            "doc_id": p.get("doc_id"),
                            "clause_id": p.get("clause_id"),
                            "framework": p.get("framework"),
                            "chunk_type": p.get("chunk_type"),
                            # Whole passage: the evaluation judge and the live scorer score
                            # claims against this text, so a cut-off copy understates support.
                            "text": p.get("text", ""),
                        }
                        for p in results
                    ],
                },
            )
        return results

    def get_clause(self, doc_id: str, clause_id: str) -> Optional[Dict[str, Any]]:
        started = time.monotonic()
        result = self.client.get_clause(doc_id, clause_id)
        latency_ms = round((time.monotonic() - started) * 1000)

        if self.store and self.run_id:
            self.store.append_event(
                run_id=self.run_id,
                agent="retrieval",
                step="get_clause",
                kind="tool_call",
                latency_ms=latency_ms,
                payload={
                    "tool": "get_clause",
                    "doc_id": doc_id,
                    "clause_id": clause_id,
                    "found": result is not None,
                    # The service returns a clause with all its chunk ids.
                    "chunk_ids": (result.get("chunk_ids") or
                                  ([result["chunk_id"]] if result.get("chunk_id") else []))
                    if result else [],
                },
            )
        return result

    def get_chunk(self, chunk_id: str, quote_span: Optional[str] = None) -> Optional[Dict[str, Any]]:
        started = time.monotonic()
        result = self.client.get_chunk(chunk_id, quote_span=quote_span)
        latency_ms = round((time.monotonic() - started) * 1000)

        if self.store and self.run_id:
            self.store.append_event(
                run_id=self.run_id,
                agent="verifier",
                step="get_chunk",
                kind="tool_call",
                latency_ms=latency_ms,
                payload={
                    "tool": "get_chunk",
                    "chunk_id": chunk_id,
                    "quote_span_checked": quote_span is not None,
                    "found": result is not None,
                },
            )
        return result

    def list_framework_scope(self, framework: Optional[str] = None) -> List[Dict[str, Any]]:
        started = time.monotonic()
        results = self.client.list_framework_scope(framework)
        latency_ms = round((time.monotonic() - started) * 1000)

        if self.store and self.run_id:
            self.store.append_event(
                run_id=self.run_id,
                agent="retrieval",
                step="list_framework_scope",
                kind="tool_call",
                latency_ms=latency_ms,
                payload={
                    "tool": "list_framework_scope",
                    "framework": framework,
                    "count": len(results) if results else 0,
                },
            )
        return results
