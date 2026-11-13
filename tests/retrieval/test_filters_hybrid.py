"""entity_type/product_type post-filters + hybrid mode against the live index."""
import pytest

from services.corpus_common import store
from services.retrieval.models import SearchFilters, SearchMode, SearchRequest
from services.retrieval.service import Retriever


@pytest.fixture(scope="module")
def retriever():
    col = store.get_collection()
    if col.count() == 0:
        pytest.skip("index not built — run `python -m services.ingest`")
    return Retriever(collection=col)


def test_entity_filter_only_returns_matching(retriever):
    resp = retriever.search(SearchRequest(
        query="grievance redressal complaint", k=8,
        filters=SearchFilters(entity_type="bank")))
    assert resp.results, "expected at least one bank-tagged chunk"
    assert all("bank" in p.entity_types for p in resp.results)


def test_scope_card_filterable_and_links_source(retriever):
    resp = retriever.search(SearchRequest(
        query="what does the scheme cover", k=20,
        filters=SearchFilters(chunk_type="scope_card")))
    assert resp.results
    assert all(p.chunk_type == "scope_card" for p in resp.results)
    assert all(p.source_clause_chunk_id for p in resp.results)


def test_hybrid_returns_results(retriever):
    resp = retriever.search(SearchRequest(
        query="demat account frozen depository participant", k=5, mode=SearchMode.hybrid))
    assert resp.mode == SearchMode.hybrid
    assert resp.count >= 1


def test_product_filter(retriever):
    resp = retriever.search(SearchRequest(
        query="deposit interest", k=8, filters=SearchFilters(product_type="fixed_deposit")))
    assert all("fixed_deposit" in p.product_types for p in resp.results)


@pytest.mark.parametrize("framework", ["SEBI", "RBI"])
def test_hybrid_respects_the_framework_filter(retriever, framework):
    """Lexical hits used to be fused in unfiltered, so the other framework leaked through."""
    resp = retriever.search(SearchRequest(
        query="ombudsman complaint deficiency in service mutual fund redemption", k=20,
        mode=SearchMode.hybrid, filters=SearchFilters(framework=framework)))
    assert resp.results
    assert {p.framework for p in resp.results} == {framework}


def test_hybrid_respects_the_chunk_type_filter(retriever):
    resp = retriever.search(SearchRequest(
        query="scheme covers regulated entities", k=20, mode=SearchMode.hybrid,
        filters=SearchFilters(chunk_type="scope_card")))
    assert resp.results and all(p.chunk_type == "scope_card" for p in resp.results)


def test_lexical_index_is_rebuilt_when_the_index_version_changes():
    class Collection:
        def __init__(self):
            self.docs = {"a": "demat account frozen", "c": "share transfer pending",
                         "d": "dividend not received"}

        def get(self, where=None, include=None, ids=None):
            return {"ids": list(self.docs), "documents": list(self.docs.values())}

    collection = Collection()
    retriever = Retriever(collection=collection, use_cache=False)
    version = ["v1"]
    retriever.index_version = lambda: version[0]

    assert retriever._bm25_ids("mutual fund redemption", None, 5) == []

    collection.docs["b"] = "mutual fund redemption delayed"
    assert retriever._bm25_ids("mutual fund redemption", None, 5) == [], "same version: cached"
    version[0] = "v2"
    assert retriever._bm25_ids("mutual fund redemption", None, 5) == ["b"]
