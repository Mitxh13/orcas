from libs.orcas_common.runstore import InMemoryRunStore
from services.worker.clients.observed_retrieval import ObservedRetrievalClient


def test_search_records_query_and_passage_for_run_trace():
    class Retrieval:
        index_version = "index-v1"

        def search(self, query, k, framework, entity_type):
            return [{"chunk_id": "chunk-1", "doc_id": "doc-1", "text": "relevant regulation"}]

    store = InMemoryRunStore()
    run_id = "run-1"
    store.create_run(run_id, {})
    client = ObservedRetrievalClient(Retrieval(), store, run_id)
    assert client.search("interest", framework="RBI")[0]["chunk_id"] == "chunk-1"
    event = store.get_events(run_id)[0]
    assert event["kind"] == "tool_call"
    assert event["payload"]["query"] == "interest"
    assert event["payload"]["passages"][0]["chunk_id"] == "chunk-1"


def test_search_event_keeps_the_whole_passage_text():
    from libs.orcas_common.runstore import InMemoryRunStore
    from services.worker.clients.observed_retrieval import ObservedRetrievalClient

    long_text = "deficiency in service " * 60

    class Retrieval:
        def search(self, query, k=5, framework=None, entity_type=None):
            return [{"chunk_id": "c1", "doc_id": "d1", "clause_id": "9", "framework": "RBI",
                     "chunk_type": "clause", "text": long_text}]

    store = InMemoryRunStore()
    store.create_run("run-1", {})
    ObservedRetrievalClient(Retrieval(), store, "run-1").search("interest")

    passage = store.get_events("run-1")[0]["payload"]["passages"][0]
    assert passage["text"] == long_text
    assert (passage["framework"], passage["chunk_type"]) == ("RBI", "clause")


def test_clause_event_records_the_chunk_ids_the_service_returns():
    from libs.orcas_common.runstore import InMemoryRunStore
    from services.worker.clients.observed_retrieval import ObservedRetrievalClient

    class Retrieval:
        def get_clause(self, doc_id, clause_id):
            return {"doc_id": doc_id, "clause_id": clause_id, "text": "t", "chunk_ids": ["a", "b"]}

    store = InMemoryRunStore()
    store.create_run("run-1", {})
    ObservedRetrievalClient(Retrieval(), store, "run-1").get_clause("d1", "9")

    payload = store.get_events("run-1")[0]["payload"]
    assert payload["found"] is True and payload["chunk_ids"] == ["a", "b"]
