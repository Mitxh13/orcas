"""The demo 5, 6 and 8 checks, run against what the real pipeline produces."""
import importlib.util
import uuid
from pathlib import Path

import pytest

from libs.orcas_common.runstore import InMemoryRunStore
from services.worker.agent.researcher import ResearcherGraph
from services.worker.agent.verifier import VerifierGraph
from services.worker.clients.fake_llm import FakeLLMClient
from services.worker.clients.fake_retrieval import FakeRetrievalClient
from services.worker.tasks import run_complaint_pipeline

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("agent_demos", ROOT / "scripts/demo/agent_demos.py")
demos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demos)

FIXTURES = str(ROOT / "tests/fixtures/retrieval_fixtures.json")


def _pipeline(text, llm, **kwargs):
    """Run the worker pipeline in-process; return (run record, events) as the gateway serves them."""
    store = InMemoryRunStore()
    run_id = str(uuid.uuid4())
    retrieval = FakeRetrievalClient(fixtures_path=FIXTURES)
    result = run_complaint_pipeline(
        run_id=run_id, complaint_data=demos.complaint(text, "test"), run_store=store,
        researcher_graph=ResearcherGraph(llm_client=llm, retrieval_client=retrieval),
        verifier_graph=VerifierGraph(llm_client=llm, retrieval_client=retrieval), **kwargs)
    return {"run_id": run_id, "status": result["status"], "result": result}, store.get_events(run_id)


OUT_OF_CORPUS_LLM = {"classify": {"entity_type": "other", "regulator": "UNDETERMINED",
                                   "product": "health_insurance", "stated_regulator": None,
                                   "regulators_involved": [], "reasoning": "insurance"}}


def test_out_of_corpus_check_passes_on_a_real_escalation():
    run, _ = _pipeline(demos.OUT_OF_CORPUS_TEXT, FakeLLMClient(overrides=OUT_OF_CORPUS_LLM))
    assert "UNDETERMINED" in demos.check_out_of_corpus(run)


def test_out_of_corpus_check_fails_when_the_complaint_is_answered():
    run, _ = _pipeline(demos.BROKER_TEXT, FakeLLMClient())
    with pytest.raises(demos.DemoFailure, match="expected an escalation"):
        demos.check_out_of_corpus(run)


def test_bad_draft_check_passes_when_the_planted_citation_is_rejected_and_redrafted():
    run, events = _pipeline(demos.BROKER_TEXT, FakeLLMClient(), inject_bad_draft=True)
    outcome = demos.check_bad_draft(run, events)
    assert "rejected the planted citation" in outcome and "accepted on attempt 2" in outcome


def test_bad_draft_check_fails_when_no_fault_was_injected():
    run, events = _pipeline(demos.BROKER_TEXT, FakeLLMClient())
    with pytest.raises(demos.DemoFailure, match="ORCAS_INJECT_BAD_DRAFT"):
        demos.check_bad_draft(run, events)


def test_bad_draft_check_fails_if_the_fabricated_citation_survives():
    run, events = _pipeline(demos.BROKER_TEXT, FakeLLMClient(), inject_bad_draft=True)
    run["result"]["citations"].append({"chunk_id": demos.FABRICATED_CHUNK})
    with pytest.raises(demos.DemoFailure, match="in the final result"):
        demos.check_bad_draft(run, events)


def test_provider_outage_check_passes_on_a_degraded_escalation():
    run, events = _pipeline(demos.BANK_TEXT, FakeLLMClient(unavailable=True))
    assert "degraded=true" in demos.check_provider_outage(run, events)


def test_provider_outage_check_fails_on_a_normal_run():
    run, events = _pipeline(demos.BROKER_TEXT, FakeLLMClient())
    with pytest.raises(demos.DemoFailure, match="expected an escalation"):
        demos.check_provider_outage(run, events)


class Gateway:
    """Scripted gateway: POST accepts, the run turns terminal on the third poll."""

    def __init__(self, final):
        self.final, self.polls, self.sent = final, 0, []

    def __call__(self, method, url, body=None, headers=None, timeout=15):
        self.sent.append((method, url, headers))
        if method == "POST":
            return 202, {"run_id": "r1", "status": "queued"}
        if url.endswith("/trace"):
            return 200, {"run_id": "r1", "events": [{"kind": "run_finished"}]}
        self.polls += 1
        return 200, self.final if self.polls >= 3 else {"run_id": "r1", "status": "running"}


def test_submit_bypasses_the_run_cache_and_polls_to_a_terminal_state():
    gateway = Gateway({"run_id": "r1", "status": "escalated", "result": {}})
    run, events = demos.submit_and_wait("http://gw", "text", "client", 30, call=gateway,
                                        sleep=lambda s: None)
    assert run["status"] == "escalated" and events == [{"kind": "run_finished"}]
    assert gateway.sent[0][2] == {"X-Run-Cache-Bypass": "true"}
    assert gateway.polls == 3


def test_run_that_never_finishes_is_a_failure():
    gateway = Gateway({"run_id": "r1", "status": "running"})
    with pytest.raises(demos.DemoFailure, match="still 'running'"):
        demos.submit_and_wait("http://gw", "text", "client", 0, call=gateway, sleep=lambda s: None)


def test_rejected_submission_is_a_failure():
    with pytest.raises(demos.DemoFailure, match="not accepted: HTTP 429"):
        demos.submit_and_wait("http://gw", "text", "client", 5,
                              call=lambda *a, **k: (429, {"detail": "slow down"}))


def test_unreachable_stack_fails_the_demo_instead_of_passing(capsys):
    assert demos.main(["out-of-corpus", "--base-url", "http://127.0.0.1:9", "--timeout", "1"]) == 1
    assert "could not be sent" in capsys.readouterr().err


def test_breaker_is_tripped_by_failing_requests_until_health_reports_open():
    state = {"failures": 0}

    def call(method, url, body=None, headers=None, timeout=15):
        if method == "POST":
            state["failures"] += 1
            return 503, {"detail": "exhausted"}
        return 200, {"circuit_breaker": "open" if state["failures"] >= 3 else "closed",
                     "failure_count": state["failures"]}

    assert demos.trip_breaker("http://llm", call=call)["failure_count"] == 3


def test_breaker_that_never_opens_is_a_failure():
    def call(method, url, body=None, headers=None, timeout=15):
        return (503, {}) if method == "POST" else (200, {"circuit_breaker": "closed"})

    with pytest.raises(demos.DemoFailure, match="breaker is 'closed'"):
        demos.trip_breaker("http://llm", call=call, attempts=2)
