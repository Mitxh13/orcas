import httpx

from services.worker.clients.llm_client import LLMClient


def test_llm_call_reports_usage_and_propagates_run_id(monkeypatch):
    seen = []
    calls = []

    def responder(request):
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.02},
            "model": "test-model",
        })

    http = httpx.Client(transport=httpx.MockTransport(responder))

    class ClientContext:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return http

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("services.worker.clients.llm_client.httpx.Client", ClientContext)
    client = LLMClient(gateway_url="http://llm-gateway:8002", run_id="run-123",
                       on_call=lambda *args: calls.append(args))
    result = client.chat([{"role": "user", "content": "classify"}],
                         agent="researcher", step="classify", prompt_version="v1")
    assert result["usage"]["cost_usd"] == 0.02
    assert seen[0].headers["X-Correlation-ID"] == "run-123"
    assert calls[0][:3] == ("researcher", "classify", "v1")
    assert calls[0][3]["usage"]["prompt_tokens"] == 10


def test_worker_waits_longer_than_the_gateway_total_budget(monkeypatch):
    from services.worker.clients.llm_client import client_timeout_s

    monkeypatch.delenv("LLM_CLIENT_TIMEOUT_S", raising=False)
    monkeypatch.delenv("LLM_TOTAL_BUDGET_SECONDS", raising=False)
    assert client_timeout_s() == 100.0
    monkeypatch.setenv("LLM_TOTAL_BUDGET_SECONDS", "240")
    assert client_timeout_s() == 250.0
    monkeypatch.setenv("LLM_CLIENT_TIMEOUT_S", "15")
    assert client_timeout_s() == 15.0
