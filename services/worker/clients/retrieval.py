"""HTTP adapter for Tahir's retrieval service contract."""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx


class HttpRetrievalClient:
    def __init__(self, base_url: str | None = None, client: httpx.Client | None = None,
                 run_id: str | None = None):
        self.base_url = (base_url or os.getenv("RETRIEVAL_SERVICE_URL", "http://localhost:8001")).rstrip("/")
        self.client = client or httpx.Client(timeout=float(os.getenv("RETRIEVAL_TIMEOUT_S", "10")))
        self.index_version = ""
        self.run_id = run_id

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Correlation-ID": self.run_id} if self.run_id else {}

    def search(self, query: str, k: int = 5, framework: str | None = None,
               entity_type: str | None = None) -> list[dict]:
        filters = {key: value for key, value in {
            "framework": framework, "entity_type": entity_type,
        }.items() if value is not None}
        response = self.client.post(f"{self.base_url}/v1/search", json={
            "query": query, "k": k, "filters": filters or None,
        }, headers=self.headers)
        response.raise_for_status()
        body = response.json()
        self.index_version = body.get("index_version", self.index_version)
        return body["results"]

    def get_clause(self, doc_id: str, clause_id: str) -> dict | None:
        response = self.client.get(
            f"{self.base_url}/v1/clauses/{quote(doc_id, safe='')}/{quote(clause_id, safe='')}",
            headers=self.headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_chunk(self, chunk_id: str, quote_span: str | None = None) -> dict | None:
        response = self.client.get(
            f"{self.base_url}/v1/chunks/{quote(chunk_id, safe='')}",
            params={"quote_span": quote_span} if quote_span is not None else None,
            headers=self.headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def list_framework_scope(self, framework: str | None = None) -> list[dict]:
        response = self.client.post(f"{self.base_url}/v1/search", json={
            "query": "regulatory framework scope and covered entities",
            "k": 8,
            "filters": {key: value for key, value in {
                "chunk_type": "scope_card", "framework": framework,
            }.items() if value is not None},
        }, headers=self.headers)
        response.raise_for_status()
        return response.json()["results"]
