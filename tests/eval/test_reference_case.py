"""§4.1 reference case (T4): one pinned reference answer, checked through the shipped agent.

The pinned passages are the addendum in docs/work/tahir/evaluation-1-package.md. If a re-ingest
changes a chunk id or quote, the first test fails and the addendum must be updated.
"""
import pytest

from eval.agent_sut import AgentSUT
from services.corpus_common import store

RBI = "rbi_integrated_ombudsman_scheme_2021"
SCENARIO = {
    "id": "ref_4_1_bank_fd",
    "complaint_text": "I invested in a fixed deposit through my bank's app. The interest credited is lower "
                      "than what I was promised. I want to complain to SEBI.",
    "expected_route": "refer_to_other_regulator", "expected_regulator": "RBI",
    "expected_entity_type": "bank", "expected_escalation": False,
}
PINNED = [  # (doc_id, clause, chunk_id, exact quote)
    ("sebi_scores_master_circular", "2",
     "d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e",
     "registered intermediaries and recognised MIIs that such investor grievances may be resolved "
     "faster if these grievances are taken up directly with the entity concerned at the first instance"),
    (RBI, "preamble", "c3cdfa20cf3464b2e00edbf53c1392dffdd8a237837b12f092cc4bdfa3bcc2b0",
     "A Scheme for resolving customer grievances in relation to services provided by entities "
     "regulated by Reserve Bank of India"),
    (RBI, "9", "17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64",
     "Any customer aggrieved by an act or omission of a Regulated Entity resulting in deficiency in "
     "service may file a complaint under the Scheme"),
]
DECIDING = {"preamble", "9"}
NON_MAINTAINABILITY = "10"


@pytest.fixture(scope="module")
def built():
    if store.get_collection().count() == 0:
        pytest.skip("index not built — run `python -m services.ingest`")
    return True


@pytest.fixture(scope="module")
def run(built):
    pytest.importorskip("langgraph")
    return AgentSUT(llm="stub").run(SCENARIO)


def test_pinned_passages_resolve_with_their_quotes(built):
    from services.retrieval.service import Retriever

    retriever = Retriever(use_cache=False)
    for doc_id, clause, chunk_id, quote in PINNED:
        found = retriever.get_clause(doc_id, clause)
        assert found is not None and found.chunk_ids == [chunk_id], (doc_id, clause)
        assert retriever.get_chunk(chunk_id, quote_span=quote).quote_span_found, (doc_id, clause)


def test_reference_case_is_referred_to_rbi(run):
    assert run.details["status"] == "completed"
    assert (run.route, run.regulator) == ("refer_to_other_regulator", "RBI")


def test_reference_case_cites_the_deciding_clauses_not_non_maintainability(run):
    rbi_clauses = {c["clause"] for c in run.details["citations"] if c["doc_id"] == RBI}
    assert rbi_clauses & DECIDING
    assert NON_MAINTAINABILITY not in rbi_clauses
