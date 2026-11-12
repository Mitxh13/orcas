from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from libs.orcas_common.runstore import InMemoryRunStore
from services.worker.tasks import sweep_stale_runs


def test_run_store_keeps_first_terminal_result_and_sequences_events():
    store = InMemoryRunStore()
    run_id = "20dedc07-67da-4770-82bd-83389b7f2004"
    store.create_run(run_id, {"text": "first"})
    store.create_run(run_id, {"text": "second"})
    assert store.get_run(run_id)["payload"] == {"text": "first"}

    assert store.claim_run(run_id) is True
    assert store.claim_run(run_id) is False
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda i: store.append_event(run_id, "system", "test", "tool_call", payload={"i": i}), range(25)))
    assert [event["seq"] for event in store.get_events(run_id)] == list(range(1, 26))

    accepted = {"status": "completed", "route": "assign_to_entity"}
    store.complete(run_id, accepted)
    store.complete(run_id, {"status": "escalated"})
    store.fail(run_id, "late retry")
    assert store.get_run(run_id)["result"] == accepted
    assert store.get_run(run_id)["status"] == "completed"


def test_quarantine_does_not_create_a_run():
    store = InMemoryRunStore()
    quarantine_id = store.quarantine({"text": "bad"}, "validation", [{"field": "text"}])
    assert store.get_quarantine(quarantine_id)["reason"] == "validation"
    assert store.runs == {}


def test_redelivered_run_can_be_claimed_and_stale_run_escalates():
    store = InMemoryRunStore()
    run_id = "78c14cdd-8f79-4cea-8cce-bb13d4f065c8"
    store.create_run(run_id, {"text": "complaint"})
    assert store.claim_run(run_id)
    assert not store.claim_run(run_id)
    assert store.claim_run(run_id, redelivered=True)
    store.runs[run_id]["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    assert sweep_stale_runs(store, max_age_s=240) == 1
    record = store.get_run(run_id)
    assert record["status"] == "escalated"
    assert record["result"]["escalation"]["reason_code"] == "agent_failure"
    assert record["result"]["degraded"] is True
    assert sweep_stale_runs(store, max_age_s=240) == 0
