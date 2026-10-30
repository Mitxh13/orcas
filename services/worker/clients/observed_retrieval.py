"""Persist retrieval calls as ordered run events."""

from __future__ import annotations

import time


class ObservedRetrievalClient:
    def __init__(self, client, store, run_id: str):
        self.client = client
        self.store = store
        self.run_id = run_id

    @property
    def index_version(self):
        return self.client.index_version

    def search(self, query, k=5, framework=None, entity_type=None):
        started = time.monotonic()
        results = self.client.search(query, k=k, framework=framework, entity_type=entity_type)
        self.store.append_event(
            self.run_id, "retrieval", "search", "tool_call",
            latency_ms=round((time.monotonic() - started) * 1000),
            payload={"query": query, "framework": framework, "entity_type": entity_type,
                     "passages": [{"chunk_id": p.get("chunk_id"), "doc_id": p.get("doc_id"),
                                   "quote_span": p.get("quote_span"), "text": p.get("text", "")[:500]}
                                  for p in results]},
        )
        return results

    def get_clause(self, doc_id, clause_id):
        return self.client.get_clause(doc_id, clause_id)

    def get_chunk(self, chunk_id, quote_span=None):
        return self.client.get_chunk(chunk_id, quote_span=quote_span)

    def list_framework_scope(self, framework=None):
        return self.client.list_framework_scope(framework)
