"""Run the initial intake demo against a running Compose stack.

Usage: python scripts/demo/initial_demo.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta


def request(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=payload, method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    filed = date.today()
    event = filed - timedelta(days=14)
    valid = {
        "client_id": "initial-demo",
        "is_synthetic": True,
        "channel": "web",
        "text": "I invested in a fixed deposit through my bank app. The interest credited is lower than promised, and I want to complain to SEBI.",
        "date_of_event": event.isoformat(),
        "date_filed": filed.isoformat(),
    }
    status, accepted = request("POST", f"{base}/v1/complaints", valid)
    if status != 202:
        raise SystemExit(f"Valid complaint failed ({status}): {accepted}")
    run_id = accepted["run_id"]
    print(f"Accepted run: {run_id}")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status, run = request("GET", f"{base}/v1/runs/{run_id}")
        if status != 200:
            raise SystemExit(f"Polling failed ({status}): {run}")
        if run["status"] in {"completed", "escalated", "failed"}:
            print(f"Terminal status: {run['status']}")
            if run.get("result"):
                print(f"Route: {run['result'].get('route')}; regulator: {run['result'].get('regulator')}")
            break
        time.sleep(2)
    else:
        raise SystemExit("Run did not reach a terminal status before timeout")

    result = run.get("result") or {}
    if (run["status"], result.get("route"), result.get("regulator")) != (
        "completed", "refer_to_other_regulator", "RBI"
    ):
        raise SystemExit("Reference bank complaint did not complete with the RBI route")

    status, trace = request("GET", f"{base}/v1/runs/{run_id}/trace")
    kinds = {event["kind"] for event in trace.get("events", [])}
    if status != 200 or not {"tool_call", "llm_call", "verify", "run_finished"} <= kinds:
        raise SystemExit(f"Run trace is missing required steps: {kinds}")
    print(f"Trace events: {len(trace['events'])}")

    malformed = dict(valid, date_filed=(event - timedelta(days=1)).isoformat())
    status, rejected = request("POST", f"{base}/v1/complaints", malformed)
    if status != 422:
        raise SystemExit(f"Malformed complaint was not rejected: {status} {rejected}")
    print(f"Rejected malformed complaint: quarantine_id={rejected['quarantine_id']}")
    print(f"Field errors: {rejected['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
