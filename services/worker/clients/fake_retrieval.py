import json
import os
from typing import Any, Dict, List, Optional


class FakeRetrievalClient:
    """
    Fake retrieval client loaded directly from Tahir's real SHA-pinned fixtures (tests/fixtures/retrieval_fixtures.json).
    Provides search, get_clause, and get_chunk matching the contracts/retrieval.openapi.yaml specification.
    """
    def __init__(self, fixtures_path: Optional[str] = None):
        if not fixtures_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            fixtures_path = os.path.join(base_dir, "tests", "fixtures", "retrieval_fixtures.json")
        
        self.fixtures_path = fixtures_path
        with open(fixtures_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.fixtures = data.get("fixtures", [])
            self.index_version = data.get("index_version", "minilm-l6-v2.clause.fixtures")

        # Build chunks and clauses map from fixtures
        self.chunks_map: Dict[str, Dict[str, Any]] = {}
        self.clauses_map: Dict[str, Dict[str, Any]] = {}
        self.all_passages: List[Dict[str, Any]] = []

        for fix in self.fixtures:
            for p in fix.get("supporting_passages", []) + fix.get("apparent_passages", []):
                chunk_id = p["chunk_id"]
                entry = {
                    "chunk_id": chunk_id,
                    "doc_id": p["doc_id"],
                    "framework": p["framework"],
                    "clause_id": p.get("clause_id", ""),
                    "text": p.get("quote", ""),
                    "score": 0.95,
                    "chunk_type": "clause"
                }
                self.chunks_map[chunk_id] = entry
                clause_key = f"{p['doc_id']}:{p.get('clause_id')}"
                self.clauses_map[clause_key] = entry
                if not any(x["chunk_id"] == chunk_id for x in self.all_passages):
                    self.all_passages.append(entry)

    def search(
        self,
        query: str,
        k: int = 5,
        framework: Optional[str] = None,
        entity_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Keyword-weighted search over loaded corpus passages."""
        q_words = set(query.lower().split())
        scored = []
        for p in self.all_passages:
            if framework and p.get("framework") != framework:
                continue
            text_words = set(p.get("text", "").lower().split())
            overlap = len(q_words.intersection(text_words))
            scored.append((overlap, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:k]]

    def get_clause(self, doc_id: str, clause_id: str) -> Optional[Dict[str, Any]]:
        key = f"{doc_id}:{clause_id}"
        return self.clauses_map.get(key)

    def get_chunk(self, chunk_id: str, quote_span: Optional[str] = None) -> Optional[Dict[str, Any]]:
        chunk = self.chunks_map.get(chunk_id)
        if not chunk:
            return None
        res = dict(chunk)
        if quote_span is not None:
            res["quote_span_found"] = quote_span.strip() in res["text"]
        return res

    def list_framework_scope(self, framework: Optional[str] = None) -> List[Dict[str, Any]]:
        cards = [
            {
                "framework": "SEBI",
                "description": "Covers registered intermediaries: stock brokers, mutual funds, listed companies, depositories.",
                "source_clause_chunk_id": "d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e"
            },
            {
                "framework": "RBI",
                "description": "Covers banks, payments systems, and fixed deposit servicing under Ombudsman Scheme.",
                "source_clause_chunk_id": "c3cdfa20cf3464b2e00edbf53c1392dffdd8a237837b12f092cc4bdfa3bcc2b0"
            }
        ]
        if framework:
            return [c for c in cards if c["framework"] == framework]
        return cards
