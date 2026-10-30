import json
from pathlib import Path

from services.gateway.input_guardrails import check_free_text
from services.gateway.models import Complaint, cross_field_errors


def test_load_payloads_match_intake_contract_and_guardrail_split():
    source = Path(__file__).parents[2] / "loadtest" / "payloads.json"
    payloads = json.loads(source.read_text(encoding="utf-8"))["payloads"]
    accepted = rejected = 0
    for item in payloads:
        payload = {key: value for key, value in item.items() if not key.startswith("_")}
        complaint = Complaint.model_validate_json(json.dumps(payload), strict=True)
        assert not cross_field_errors(complaint)
        flagged = check_free_text({"text": complaint.text, "entity_name_hint": complaint.entity_name_hint})
        if item["_scenario_id"].startswith("prompt_injection"):
            assert flagged
            rejected += 1
        else:
            assert not flagged
            accepted += 1
    assert (accepted, rejected) == (20, 2)
