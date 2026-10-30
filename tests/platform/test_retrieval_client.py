import httpx

from services.worker.clients.retrieval import HttpRetrievalClient


def test_worker_retrieval_adapter_uses_live_api_contract():
    seen = []

    def responder(request):
        seen.append(request)
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={
                "index_version": "index-42", "results": [{"chunk_id": "chunk-1", "text": "RBI passage"}],
            })
        if request.url.path == "/v1/chunks/chunk-1":
            return httpx.Response(200, json={"chunk_id": "chunk-1", "text": "RBI passage"})
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(responder))
    retrieval = HttpRetrievalClient(base_url="http://retrieval:8001", client=http, run_id="run-123")
    assert retrieval.search("bank deposits", framework="RBI", k=3)[0]["chunk_id"] == "chunk-1"
    assert retrieval.index_version == "index-42"
    assert seen[0].read().decode().count('"RBI"') == 1
    assert seen[0].headers["X-Correlation-ID"] == "run-123"
    assert retrieval.get_chunk("chunk-1")["text"] == "RBI passage"
    assert retrieval.get_clause("missing", "p1") is None
