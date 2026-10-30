import json
from pathlib import Path

from fastapi.testclient import TestClient

from libs.orcas_common.runstore import InMemoryRunStore
from services.gateway.app import create_app
from services.gateway.models import Complaint


class RecordingDispatcher:
    def __init__(self):
        self.sent = []

    def send(self, run_id):
        self.sent.append(run_id)

    def ping(self):
        return True


def client_and_state():
    store = InMemoryRunStore()
    dispatcher = RecordingDispatcher()
    return TestClient(create_app(store=store, dispatcher=dispatcher)), store, dispatcher


def valid_payload():
    return {
        "client_id": "demo-client",
        "is_synthetic": True,
        "channel": "web",
        "text": "I invested in a fixed deposit through my bank app and received less interest than promised.",
        "date_of_event": "2026-08-01",
        "date_filed": "2026-09-01",
    }


def test_accepts_valid_request_and_polls_run():
    client, store, dispatcher = client_and_state()
    response = client.post("/v1/complaints", json=valid_payload())
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.json()["status_url"] == f"/v1/runs/{run_id}"
    assert response.headers["X-Correlation-ID"] == run_id
    assert dispatcher.sent == [run_id]
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "queued"
    store.update_status(run_id, "running")
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "running"
    store.complete(run_id, {"status": "completed", "route": "assign_to_entity"})
    terminal = client.get(f"/v1/runs/{run_id}").json()
    assert terminal["status"] == "completed"
    assert terminal["result"]["route"] == "assign_to_entity"


def test_rejects_and_quarantines_before_run_or_enqueue():
    client, store, dispatcher = client_and_state()
    payload = valid_payload()
    payload["date_filed"] = "2026-07-01"
    response = client.post("/v1/complaints", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "date_filed"
    assert store.get_quarantine(response.json()["quarantine_id"])["reason"] == "validation"
    assert store.runs == {}
    assert dispatcher.sent == []


def test_rejects_bad_json_and_schema_errors_before_enqueue():
    client, store, dispatcher = client_and_state()
    invalid_json = client.post("/v1/complaints", content=b"{", headers={"Content-Type": "application/json"})
    assert invalid_json.status_code == 422
    assert store.get_quarantine(invalid_json.json()["quarantine_id"])

    payload = valid_payload()
    payload["is_synthetic"] = False
    payload["client_id"] = "!"
    payload["amount_disputed_inr"] = "25"
    response = client.post("/v1/complaints", json=payload)
    assert response.status_code == 422
    assert {error["field"] for error in response.json()["detail"]} == {
        "client_id", "is_synthetic", "amount_disputed_inr"
    }
    assert store.runs == {}
    assert dispatcher.sent == []


def test_prior_complaint_date_is_required_when_prior_contact_is_true():
    client, store, dispatcher = client_and_state()
    payload = valid_payload()
    payload["prior_complaint_to_entity"] = True
    response = client.post("/v1/complaints", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "prior_complaint_date"
    assert store.runs == {}
    assert dispatcher.sent == []


def test_request_model_tracks_frozen_schema_fields():
    schema = json.loads((Path(__file__).parents[2] / "contracts" / "complaint.v1.schema.json").read_text())
    assert set(Complaint.model_fields) == set(schema["properties"])
    assert {name for name, field in Complaint.model_fields.items() if field.is_required()} == set(schema["required"])


def test_trace_reconstructs_ordered_events_and_cost():
    client, store, _ = client_and_state()
    run_id = client.post("/v1/complaints", json=valid_payload()).json()["run_id"]
    store.append_event(run_id, "researcher", "search", "tool_call", payload={"query": "FD interest"}, cost_usd=0.01)
    store.append_event(run_id, "verifier", "verify", "verification", payload={"passed": True}, cost_usd=0.02)
    trace = client.get(f"/v1/runs/{run_id}/trace")
    assert trace.status_code == 200
    assert [event["seq"] for event in trace.json()["events"]] == [1, 2]
    assert trace.json()["events"][0]["payload"]["query"] == "FD interest"
    assert round(trace.json()["total_cost_usd"], 2) == 0.03


def test_feedback_is_persisted_and_aggregated(monkeypatch):
    monkeypatch.setenv("ORCAS_ADMIN_TOKEN", "test-admin-token")
    client, _, _ = client_and_state()
    run_id = client.post("/v1/complaints", json=valid_payload()).json()["run_id"]
    response = client.post(f"/v1/runs/{run_id}/feedback", json={"rating": "down", "reason": "wrong route"})
    assert response.status_code == 201
    assert response.json()["rating"] == "down"
    assert client.post(f"/v1/runs/{run_id}/feedback", json={"rating": "invalid"}).status_code == 422
    assert client.get("/v1/admin/feedback/summary").status_code == 401
    summary = client.get("/v1/admin/feedback/summary", headers={"Authorization": "Bearer test-admin-token"})
    assert summary.json() == {"up": 0, "down": 1, "total": 1}


def test_eval_score_is_persisted_for_quality_dashboard():
    client, store, _ = client_and_state()
    run_id = client.post("/v1/complaints", json=valid_payload()).json()["run_id"]
    score = store.add_eval_score(run_id, "verification_passed", 1.0,
                                 {"source": "worker_verifier"})
    assert score["metric"] == "verification_passed"
    assert store.eval_scores[0]["run_id"] == run_id


def test_quarantine_listing_requires_admin_token(monkeypatch):
    monkeypatch.setenv("ORCAS_ADMIN_TOKEN", "test-admin-token")
    client, _, _ = client_and_state()
    payload = valid_payload()
    payload["is_synthetic"] = False
    client.post("/v1/complaints", json=payload)
    assert client.get("/v1/admin/quarantine").status_code == 401
    response = client.get("/v1/admin/quarantine", headers={"Authorization": "Bearer test-admin-token"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["reason"] == "validation"


def test_rate_limit_rejects_before_run_creation():
    class Limited:
        def retry_after(self, client_id):
            assert client_id == "demo-client"
            return 3

    store = InMemoryRunStore()
    dispatcher = RecordingDispatcher()
    client = TestClient(create_app(store=store, dispatcher=dispatcher, limiter=Limited()))
    response = client.post("/v1/complaints", json=valid_payload())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"
    assert store.runs == {}
    assert dispatcher.sent == []
    assert b'orcas_gateway_rate_limit_rejections_total' in client.get("/metrics").content


def test_completed_cache_hit_creates_terminal_run_without_dispatch():
    class Cache:
        def key_for(self, payload):
            assert payload["client_id"] == "demo-client"
            return "cache-key"

        def get(self, key):
            assert key == "cache-key"
            return {"status": "completed", "run_id": "old-run", "route": "assign_to_entity"}

    store = InMemoryRunStore()
    dispatcher = RecordingDispatcher()
    client = TestClient(create_app(store=store, dispatcher=dispatcher, cache=Cache()))
    response = client.post("/v1/complaints", json=valid_payload())
    assert response.status_code == 202
    assert response.headers["X-Run-Cache"] == "hit"
    run_id = response.json()["run_id"]
    assert client.get(f"/v1/runs/{run_id}").json()["result"]["run_id"] == run_id
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "completed"
    assert dispatcher.sent == []
    assert store.get_events(run_id)[0]["kind"] == "cache_hit"


def test_cache_can_be_bypassed_for_evaluation():
    class Cache:
        def key_for(self, payload):
            raise AssertionError("cache should not be queried")

    store = InMemoryRunStore()
    dispatcher = RecordingDispatcher()
    client = TestClient(create_app(store=store, dispatcher=dispatcher, cache=Cache()))
    response = client.post("/v1/complaints", json=valid_payload(),
                           headers={"X-Run-Cache-Bypass": "true"})
    assert response.status_code == 202
    assert dispatcher.sent == [response.json()["run_id"]]
