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
