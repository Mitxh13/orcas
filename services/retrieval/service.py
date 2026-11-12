"""Retrieval logic over Chroma. Pure functions the FastAPI app wraps.

Dense OR hybrid (dense+BM25, RRF-fused) search. The dense search runs through a LangChain
retriever (langchain_retriever.py); scalar filters (framework/doc_id/chunk_type) are pushed to
Chroma; entity_type/product_type are applied as post-filters (they are list-valued metadata, so
Chroma equality can't express them). Also chunk resolution with quote-span verification, clause
lookup and index info.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from services.corpus_common import store

from .cache import get_cache, make_key
from .hybrid import BM25Index, rrf_fuse
from .langchain_retriever import SCORE_KEY, OrcasDenseRetriever
from .models import (
    Chunk,
    Clause,
    IndexDoc,
    IndexInfo,
    Passage,
    SearchMode,
    SearchRequest,
    SearchResponse,
)

_SCALAR_FILTERS = ("framework", "doc_id", "chunk_type")


def _build_where(filters) -> Optional[Dict[str, Any]]:
    if not filters:
        return None
    clauses: List[Dict[str, Any]] = []
    data = filters.model_dump(exclude_none=True)
    for key in _SCALAR_FILTERS:
        if key in data:
            val = data[key]
            clauses.append({key: getattr(val, "value", val)})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _passage_from(cid: str, text: str, meta: Dict[str, Any], score: float) -> Passage:
    meta = store.decode_metadata(meta)
    return Passage(
        chunk_id=cid,
        doc_id=meta.get("doc_id", ""),
        doc_title=meta.get("doc_title", ""),
        circular_ref=meta.get("circular_ref", ""),
        framework=meta.get("framework") or None,
        clause_id=meta.get("clause_id", ""),
        heading_path=meta.get("heading_path", []),
        chunk_type=meta.get("chunk_type", "clause"),
        entity_types=meta.get("entity_types", []),
        product_types=meta.get("product_types", []),
        version=meta.get("version", ""),
        text=text,
        score=round(float(score), 6),
        source_clause_chunk_id=meta.get("source_clause_chunk_id"),
    )


def _passes_post_filter(p: Passage, filters) -> bool:
    if not filters:
        return True
    et = getattr(filters.entity_type, "value", filters.entity_type) if filters.entity_type else None
    pt = filters.product_type
    if et and et not in (p.entity_types or []):
        return False
    if pt and pt not in (p.product_types or []):
        return False
    return True


class Retriever:
    def __init__(self, collection=None, cache=None, use_cache: bool = True):
        self._col = collection
        self._bm25: Optional[BM25Index] = None
        self._bm25_version: Optional[str] = None
        self._cache = cache if cache is not None else (get_cache() if use_cache else None)

    @property
    def col(self):
        if self._col is None:
            self._col = store.get_collection()
        return self._col

    def index_version(self) -> str:
        return (store.read_index_info() or {}).get("index_version", "")

    def _bm25_index(self) -> BM25Index:
        """Lexical index over the whole collection, rebuilt when a re-ingest changes the index."""
        version = self.index_version()
        if self._bm25 is None or version != self._bm25_version:
            data = self.col.get(include=["documents"])
            self._bm25 = BM25Index(data["ids"], data["documents"])
            self._bm25_version = version
        return self._bm25

    def _bm25_ids(self, query: str, where, n: int) -> List[str]:
        """Top-n lexical hits that satisfy the same metadata filter as the dense search."""
        index = self._bm25_index()
        if not where:
            return [cid for cid, _ in index.query(query, n)]
        allowed = set(self.col.get(where=where, include=[])["ids"])
        return [cid for cid, _ in index.query(query, len(index.ids)) if cid in allowed][:n]

    def _dense_pool(self, query: str, where, n: int):
        docs = OrcasDenseRetriever(collection=self.col, k=n, where=where).invoke(query)
        ids = [d.id for d in docs]
        pool = {}
        for d in docs:
            meta = {k: v for k, v in d.metadata.items() if k != SCORE_KEY}
            pool[d.id] = (d.page_content, meta, d.metadata[SCORE_KEY])
        return ids, pool

    def search(self, req: SearchRequest) -> SearchResponse:
        t0 = time.time()
        filters_dict = req.filters.model_dump(exclude_none=True) if req.filters else {}
        key = make_key(req.query, filters_dict, req.mode.value, req.k, self.index_version())
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                resp = SearchResponse(**cached)
                resp.cache_hit = True
                resp.latency_ms = round((time.time() - t0) * 1000, 2)
                return resp

        where = _build_where(req.filters)
        pool_n = min(50, max(req.k * 4, 20))

        dense_ids, pool = self._dense_pool(req.query, where, pool_n)

        if req.mode == SearchMode.hybrid:
            bm25_ids = self._bm25_ids(req.query, where, pool_n)
            # fetch metadata/text for bm25-only ids
            missing = [cid for cid in bm25_ids if cid not in pool]
            if missing:
                got = self.col.get(ids=missing, include=["documents", "metadatas"])
                for j, cid in enumerate(got["ids"]):
                    pool[cid] = (got["documents"][j], got["metadatas"][j], 0.0)
            fused = rrf_fuse([dense_ids, bm25_ids])
            ordered = sorted(fused, key=lambda c: fused[c], reverse=True)
            scored = [(cid, fused[cid]) for cid in ordered if cid in pool]
        else:
            scored = [(cid, pool[cid][2]) for cid in dense_ids]

        passages: List[Passage] = []
        for cid, score in scored:
            text, meta, _ = pool[cid]
            p = _passage_from(cid, text, meta, score)
            if _passes_post_filter(p, req.filters):
                passages.append(p)
            if len(passages) >= req.k:
                break

        resp = SearchResponse(
            query_normalized=" ".join(req.query.lower().split()),
            mode=req.mode,
            index_version=self.index_version(),
            count=len(passages),
            cache_hit=False,
            latency_ms=round((time.time() - t0) * 1000, 2),
            results=passages,
        )
        if self._cache is not None:
            self._cache.set(key, resp.model_dump(mode="json"))
        return resp

    def get_chunk(self, chunk_id: str, quote_span: Optional[str] = None) -> Optional[Chunk]:
        res = self.col.get(ids=[chunk_id], include=["documents", "metadatas"])
        if not res.get("ids"):
            return None
        text = res["documents"][0]
        meta = store.decode_metadata(res["metadatas"][0])
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=meta.get("doc_id", ""),
            doc_title=meta.get("doc_title", ""),
            circular_ref=meta.get("circular_ref", ""),
            framework=meta.get("framework") or None,
            clause_id=meta.get("clause_id", ""),
            heading_path=meta.get("heading_path", []),
            chunk_type=meta.get("chunk_type", "clause"),
            entity_types=meta.get("entity_types", []),
            product_types=meta.get("product_types", []),
            version=meta.get("version", ""),
            model_tag=meta.get("model_tag", ""),
            text=text,
        )
        if quote_span is not None:
            chunk.quote_span_found = quote_span in text
        return chunk

    def get_clause(self, doc_id: str, clause_id: str) -> Optional[Clause]:
        res = self.col.get(
            where={"$and": [{"doc_id": doc_id}, {"clause_id": clause_id}]},
            include=["documents", "metadatas"],
        )
        if not res.get("ids"):
            return None
        meta = store.decode_metadata(res["metadatas"][0])
        text = "\n".join(res["documents"])
        return Clause(
            doc_id=doc_id,
            clause_id=clause_id,
            doc_title=meta.get("doc_title", ""),
            circular_ref=meta.get("circular_ref", ""),
            framework=meta.get("framework") or None,
            heading_path=meta.get("heading_path", []),
            version=meta.get("version", ""),
            text=text,
            chunk_ids=list(res["ids"]),
        )

    def info(self) -> Optional[IndexInfo]:
        raw = store.read_index_info()
        if not raw:
            return None
        docs = [
            IndexDoc(
                doc_id=d.get("doc_id", ""),
                title=d.get("title", ""),
                framework=d.get("framework") or None,
                version=d.get("version", ""),
                chunk_count=d.get("chunk_count", 0),
                has_text_layer=d.get("has_text_layer"),
                source_sha256=d.get("source_sha256", ""),
            )
            for d in raw.get("documents", [])
        ]
        return IndexInfo(
            index_version=raw.get("index_version", ""),
            built_at=raw.get("built_at", ""),
            model_tag=raw.get("model_tag", ""),
            embedding_model=raw.get("embedding_model", ""),
            distance_metric=raw.get("distance_metric", ""),
            hybrid_enabled=raw.get("hybrid_enabled", False),
            chunk_count=raw.get("chunk_count", 0),
            documents=docs,
        )
