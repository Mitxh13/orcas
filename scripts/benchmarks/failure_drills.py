"""Exercise queue, broker, and retrieval failures against a disposable Compose stack."""

import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = "http://localhost:8000"
TERMINAL = {"completed", "escalated", "failed"}


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def request(method, path, body=None, headers=None):
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=payload, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def complaint(client_id):
    filed = date.today()
    return {
        "client_id": client_id,
        "is_synthetic": True,
        "channel": "web",
        "text": "My bank fixed deposit interest was lower than promised in the bank app. I want to complain to SEBI about the missing interest.",
        "date_of_event": (filed - timedelta(days=14)).isoformat(),
        "date_filed": filed.isoformat(),
    }


def submit(client_id):
    status, body = request(
        "POST", "/v1/complaints", complaint(client_id),
        {"X-Run-Cache-Bypass": "true"},
    )
    assert status == 202, (status, body)
    return body["run_id"]


def poll(run_id, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = request("GET", f"/v1/runs/{run_id}")
        assert status == 200, (status, body)
        if body["status"] in TERMINAL:
            return body
        time.sleep(2)
    raise TimeoutError(f"run {run_id} stayed nonterminal for {timeout} seconds")


def wait_retrieval(timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:8001/ready", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(3)
    raise TimeoutError("retrieval did not become ready")


def worker_drill():
    compose("stop", "worker")
    try:
        run_id = submit("drill-worker")
        time.sleep(5)
        status, queued = request("GET", f"/v1/runs/{run_id}")
        assert status == 200 and queued["status"] == "queued", queued
    finally:
        compose("start", "worker")
    result = poll(run_id)
    assert result["status"] in {"completed", "escalated"}, result
    return {"run_id": run_id, "status_while_stopped": "queued", "status_after_restart": result["status"]}


def redis_drill():
    compose("stop", "redis")
    try:
        status, body = request("POST", "/v1/complaints", complaint("drill-redis"))
        assert status == 503, (status, body)
    finally:
        compose("start", "redis")
    time.sleep(10)
    run_id = submit("drill-redis-recovered")
    result = poll(run_id)
    assert result["status"] in {"completed", "escalated"}, result
    return {"outage_http_status": status, "recovery_run_id": run_id,
            "recovery_status": result["status"]}


def retrieval_drill():
    compose("stop", "retrieval")
    try:
        run_id = submit("drill-retrieval")
        outage = poll(run_id, timeout=150)
        assert outage["status"] in TERMINAL, outage
    finally:
        compose("start", "retrieval")
    wait_retrieval()
    recovery_id = submit("drill-retrieval-recovered")
    recovery = poll(recovery_id)
    assert recovery["status"] in {"completed", "escalated"}, recovery
    return {"outage_run_id": run_id, "outage_status": outage["status"],
            "recovery_run_id": recovery_id, "recovery_status": recovery["status"]}


if __name__ == "__main__":
    results = {}
    output = Path("loadtest/results/ci/failure-drills.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    for name, drill in (("worker", worker_drill), ("redis", redis_drill),
                        ("retrieval", retrieval_drill)):
        try:
            results[name] = drill()
        except Exception as exc:
            results[name] = {"error": str(exc)}
            output.write_text(json.dumps(results, indent=2))
            raise
        output.write_text(json.dumps(results, indent=2))
        print(f"{name}: {results[name]}", flush=True)
