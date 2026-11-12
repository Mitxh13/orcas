import json

from services.llm_stub.app import ChatMessage, ChatRequest, generate_stub_reply


def test_stub_draft_cites_the_current_retrieved_chunk():
    ledger = [
        {"chunk_id": "sebi-current", "framework": "SEBI", "doc_id": "sebi_scores",
         "clause_id": "p1", "text": "Investor grievances for registered intermediaries."},
        {"chunk_id": "rbi-current", "framework": "RBI", "doc_id": "rbi_ombudsman",
         "clause_id": "p6", "doc_title": "RBI scheme", "circular_ref": "RBI-2021",
         "text": "Any customer aggrieved by deficiency in service may file a complaint."},
    ]
    prompt = "Complaint: My bank fixed deposit interest is wrong.\nEvidence Ledger: " + json.dumps(ledger)
    request = ChatRequest(messages=[ChatMessage(role="user", content=prompt)], step="draft")
    draft = json.loads(generate_stub_reply(request))
    assert draft["route"] == "refer_to_other_regulator"
    assert draft["citations"][0]["chunk_id"] == "rbi-current"
    assert draft["citations"][0]["quote_span"] in ledger[1]["text"]
    assert draft["timeline"] is None


def test_stub_draft_does_not_invent_a_citation_when_retrieval_is_empty():
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="Complaint: My bank fixed deposit is wrong.\nEvidence Ledger: []")],
        step="draft",
    )
    draft = json.loads(generate_stub_reply(request))
    assert draft["citations"] == []


def _draft(ledger, complaint="My bank fixed deposit interest is wrong."):
    prompt = f"Complaint: {complaint}\nEvidence Ledger: " + json.dumps(ledger)
    request = ChatRequest(messages=[ChatMessage(role="user", content=prompt)], step="draft")
    return json.loads(generate_stub_reply(request))


def _classify(complaint):
    request = ChatRequest(messages=[ChatMessage(role="user", content=f"Complaint: {complaint}")],
                          step="classify")
    return json.loads(generate_stub_reply(request))


def test_stub_draft_prefers_the_clause_that_decides_jurisdiction():
    ledger = [
        {"chunk_id": "c8", "framework": "RBI", "clause_id": "8",
         "text": "8. Powers and Functions: complaints relating to deficiency in service."},
        {"chunk_id": "c10", "framework": "RBI", "clause_id": "10",
         "text": "10. Grounds for non-maintainability of a Complaint: deficiency in service."},
        {"chunk_id": "c9", "framework": "RBI", "clause_id": "9",
         "text": "9. Any customer aggrieved by deficiency in service may file a complaint."},
    ]
    assert _draft(ledger)["citations"][0]["clause"] == "9"


def test_stub_draft_cites_non_maintainability_only_when_nothing_else_was_retrieved():
    clause_10 = {"chunk_id": "c10", "framework": "RBI", "clause_id": "10",
                 "text": "10. Grounds for non-maintainability: deficiency in service claims."}
    clause_8 = {"chunk_id": "c8", "framework": "RBI", "clause_id": "8",
                "text": "8. Powers and Functions of the Ombudsman."}
    assert _draft([clause_10, clause_8])["citations"][0]["clause"] == "8"
    assert _draft([clause_10])["citations"][0]["clause"] == "10"


def test_stub_keywords_match_whole_words_only():
    # "rta" in "important", "nav" in "unavailable", "atm" in "treatment", "dp" in "feedpost"
    plain = _classify("It is important that the unavailable statement gets fair treatment by my broker.")
    assert (plain["entity_type"], plain["regulator"]) == ("broker", "SEBI")
    assert not plain.get("regulators_involved")


def test_stub_keywords_still_match_plurals_and_real_abbreviations():
    assert _classify("Both brokers ignored my complaint.")["entity_type"] == "broker"
    assert _classify("My RTA has not updated my name.".lower())["entity_type"] == "registrar"
