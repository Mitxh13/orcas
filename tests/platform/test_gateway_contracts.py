"""Validate gateway responses against the published JSON Schemas."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from services.worker.tasks import _build_degraded_escalation
from tests.platform.test_gateway import client_and_state, valid_payload

CONTRACTS = Path(__file__).parents[2] / "contracts"


def schema(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_run_status_trace_and_feedback_match_contracts():
    client, store, _ = client_and_state()
    run_id = client.post("/v1/complaints", json=valid_payload()).json()["run_id"]
    status_schema = schema("run_status.v1.schema.json")
    determination_schema = schema("determination.v1.schema.json")
    registry = Registry().with_resource(
        determination_schema["$id"], Resource.from_contents(determination_schema),
    )
    status_validator = Draft202012Validator(status_schema, registry=registry)
    status_validator.validate(client.get(f"/v1/runs/{run_id}").json())
    store.claim_run(run_id)
    status_validator.validate(client.get(f"/v1/runs/{run_id}").json())

    store.append_event(run_id, "system", "recovery", "degraded", payload={"reason": "test"})
    trace = client.get(f"/v1/runs/{run_id}/trace").json()
    Draft202012Validator(schema("trace.v1.schema.json")).validate(trace)

    result = _build_degraded_escalation(run_id, None, "agent_failure", "test outage", [])
    store.complete(run_id, result)
    status_validator.validate(client.get(f"/v1/runs/{run_id}").json())

    feedback_schema = schema("feedback.v1.schema.json")
    request = {"rating": "down", "reason": "needs review"}
    Draft202012Validator(feedback_schema).validate(request)
    response = client.post(f"/v1/runs/{run_id}/feedback", json=request)
    assert response.status_code == 201
    Draft202012Validator(feedback_schema).validate(response.json())
