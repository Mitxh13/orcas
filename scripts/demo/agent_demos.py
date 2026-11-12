"""Agent demos 5, 6 and 8 against a running Compose stack.

Each demo submits a synthetic complaint through the public gateway, waits for the run to finish
and checks the stored result and trace. A demo exits non-zero with the reason if any check fails;
a request that cannot be sent is a failure, never a pass.

    python scripts/demo/agent_demos.py out-of-corpus      # demo 5
    python scripts/demo/agent_demos.py bad-draft          # demo 6
    python scripts/demo/agent_demos.py provider-outage    # demo 8

Demos 6 and 8 need a fault switched on in one service. By default the script recreates that
service with the fault through `docker compose`, runs the check, and restores the service
afterwards. Pass --no-compose when the fault is already configured (or the stack is not managed
by Compose from this directory); the script then only submits and checks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

TERMINAL = {"completed", "escalated", "failed"}
FABRICATED_CHUNK = "non_existent_chunk_hash_123456"

OUT_OF_CORPUS_TEXT = (
    "My health insurer rejected my cashless hospitalization claim at the hospital without giving "
    "any valid justification. I need my medical expenses refunded."
)
BROKER_TEXT = "My stock broker refused to process my withdrawal request for the sale proceeds."
BANK_TEXT = (
    "I invested in a fixed deposit through my bank app. The interest credited is lower than "
    "promised, and I want to complain to SEBI."
)


class DemoFailure(Exception):
    """A demo expectation was not met."""


def http(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: float = 15) -> tuple[int, dict]:
    """One JSON request. A non-2xx reply is returned; a request that cannot be sent raises."""
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method, headers={
        **({"Content-Type": "application/json"} if payload is not None else {}), **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except ValueError:
            return error.code, {}
    except (urllib.error.URLError, OSError) as error:
        raise DemoFailure(f"{method} {url} could not be sent: {error}") from error


def complaint(text: str, client_id: str) -> dict:
    filed = date.today()
    return {
        "client_id": client_id, "is_synthetic": True, "channel": "web", "text": text,
        "date_of_event": (filed - timedelta(days=14)).isoformat(), "date_filed": filed.isoformat(),
    }


def submit_and_wait(base: str, text: str, client_id: str, timeout: float,
                    call=http, sleep=time.sleep) -> tuple[dict, list[dict]]:
    """Submit a fresh (uncached) run; return its final status record and its trace events."""
    status, accepted = call("POST", f"{base}/v1/complaints", complaint(text, client_id),
                            {"X-Run-Cache-Bypass": "true"})
    if status != 202 or "run_id" not in accepted:
        raise DemoFailure(f"complaint was not accepted: HTTP {status} {accepted}")
    run_id = accepted["run_id"]
    print(f"Accepted run {run_id}")

    deadline = time.monotonic() + timeout
    while True:
        status, run = call("GET", f"{base}/v1/runs/{run_id}")
        if status != 200:
            raise DemoFailure(f"polling run {run_id} failed: HTTP {status} {run}")
        if run.get("status") in TERMINAL:
            break
        if time.monotonic() >= deadline:
            raise DemoFailure(f"run {run_id} is still '{run.get('status')}' after {timeout:.0f} s")
        sleep(2)

    status, trace = call("GET", f"{base}/v1/runs/{run_id}/trace")
    if status != 200:
        raise DemoFailure(f"trace for run {run_id} is unavailable: HTTP {status}")
    return run, trace.get("events", [])


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


def _result(run: dict) -> dict:
    result = run.get("result")
    _expect(isinstance(result, dict), f"run finished as '{run.get('status')}' with no result")
    return result


def check_out_of_corpus(run: dict) -> str:
    """Demo 5: a product outside the corpus is escalated with no regulator and no citation."""
    result = _result(run)
    escalation = result.get("escalation") or {}
    _expect(run["status"] == "escalated", f"expected an escalation, got status '{run['status']}'")
    _expect(result.get("route") == "escalate_to_human", f"route is {result.get('route')!r}")
    _expect(result.get("regulator") == "UNDETERMINED",
            f"a regulator was named for an out-of-corpus complaint: {result.get('regulator')!r}")
    _expect(escalation.get("reason_code") in {"out_of_corpus", "low_confidence"},
            f"unexpected escalation reason {escalation.get('reason_code')!r}")
    _expect(result.get("citations") == [], "an escalated out-of-corpus run carries citations")
    _expect(result.get("disposed") is False, "the complaint was marked disposed")
    return f"escalated to a human, regulator UNDETERMINED, reason {escalation['reason_code']}"


def check_bad_draft(run: dict, events: list[dict]) -> str:
    """Demo 6: the verifier rejects the planted citation and it never reaches the result."""
    result = _result(run)
    retries = [e for e in events if e.get("kind") == "retry"]
    _expect(retries, "no retry event: the planted citation was not rejected by the verifier "
                     "(is the worker running with ORCAS_INJECT_BAD_DRAFT=1?)")
    reason = str((retries[0].get("payload") or {}).get("reason") or "")
    _expect(FABRICATED_CHUNK in reason, f"the first rejection was not about the planted citation: {reason!r}")
    cited = [c.get("chunk_id") for c in result.get("citations") or []]
    _expect(FABRICATED_CHUNK not in cited, "the fabricated citation is in the final result")
    strategy = (retries[0].get("payload") or {}).get("strategy")
    drafts = sum(1 for e in events if e.get("kind") == "draft")

    if run["status"] == "completed":
        _expect(drafts >= 2, "the run completed without a second draft")
        _expect((result.get("verification") or {}).get("passed") is True,
                "the run completed although verification did not pass")
        return (f"verifier rejected the planted citation; researcher retried with '{strategy}' "
                f"and the new draft was accepted on attempt {result['verification'].get('attempts')}")
    _expect(run["status"] == "escalated", f"unexpected final status '{run['status']}'")
    _expect((result.get("escalation") or {}).get("reason_code") == "unverified",
            f"escalated for {(result.get('escalation') or {}).get('reason_code')!r}, not 'unverified'")
    return f"verifier rejected the planted citation; retry '{strategy}' did not fix it; escalated as unverified"


def check_provider_outage(run: dict, events: list[dict]) -> str:
    """Demo 8: with the provider failing, the run is escalated and marked degraded."""
    result = _result(run)
    escalation = result.get("escalation") or {}
    _expect(run["status"] == "escalated", f"expected an escalation, got status '{run['status']}'")
    _expect(result.get("degraded") is True, "the result is not marked degraded")
    _expect(escalation.get("reason_code") in {"agent_failure", "verification_unavailable"},
            f"unexpected escalation reason {escalation.get('reason_code')!r}")
    _expect(result.get("citations") == [], "a degraded escalation carries citations")
    _expect(any(e.get("kind") == "degraded" for e in events), "no 'degraded' event in the trace")
    return f"escalated to a human with degraded=true, reason {escalation['reason_code']}"


def trip_breaker(llm_gateway: str, call=http, attempts: int = 8) -> dict:
    """Send failing chat requests until the gateway reports its circuit breaker open."""
    probe = {"messages": [{"role": "user", "content": "breaker probe"}], "agent": "demo", "step": "breaker"}
    health: dict = {}
    for attempt in range(1, attempts + 1):
        status, health = call("GET", f"{llm_gateway}/health")
        _expect(status == 200, f"LLM gateway health returned HTTP {status}")
        if health.get("circuit_breaker") == "open":
            return health
        status, _ = call("POST", f"{llm_gateway}/v1/chat", probe, timeout=120)
        _expect(status == 503, f"a forced-failure chat request returned HTTP {status}, expected 503")
        print(f"  failing request {attempt}: HTTP 503")
    status, health = call("GET", f"{llm_gateway}/health")
    _expect(health.get("circuit_breaker") == "open",
            f"breaker is '{health.get('circuit_breaker')}' after {attempts} failed requests")
    return health


def compose_recreate(service: str, env: dict[str, str]) -> None:
    """Recreate one Compose service with extra environment and wait until it is healthy."""
    print(f"docker compose: recreating {service} with {env or 'its normal settings'}")
    completed = subprocess.run(
        ["docker", "compose", "up", "-d", "--wait", "--no-deps", "--force-recreate", service],
        env={**os.environ, **env}, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise DemoFailure(f"could not recreate {service}: {completed.stderr.strip()[-600:]}")


def demo_out_of_corpus(args) -> str:
    run, _ = submit_and_wait(args.base_url, OUT_OF_CORPUS_TEXT, "demo-5-out-of-corpus", args.timeout)
    return check_out_of_corpus(run)


def demo_bad_draft(args) -> str:
    if not args.no_compose:
        compose_recreate("worker", {"ORCAS_INJECT_BAD_DRAFT": "1"})
    try:
        run, events = submit_and_wait(args.base_url, BROKER_TEXT, "demo-6-bad-draft", args.timeout)
        return check_bad_draft(run, events)
    finally:
        if not args.no_compose:
            compose_recreate("worker", {"ORCAS_INJECT_BAD_DRAFT": "0"})


def demo_provider_outage(args) -> str:
    if not args.no_compose:
        # Live mode with every provider call forced to fail: no model or key is contacted.
        compose_recreate("llm-gateway", {
            "ORCAS_STUB_MODE": "false", "LLM_FORCE_FAIL": "1", "LLM_MODEL": "forced/outage",
            "LLM_FALLBACK_MODEL": "",
        })
    try:
        status, health = http("GET", f"{args.llm_gateway_url}/health")
        _expect(status == 200 and health.get("force_fail") is True and health.get("stub_mode") is False,
                f"LLM gateway is not in forced-failure mode: {health}")
        run, events = submit_and_wait(args.base_url, BANK_TEXT, "demo-8-provider-outage", args.timeout)
        outcome = check_provider_outage(run, events)
        health = trip_breaker(args.llm_gateway_url)
        return f"{outcome}; circuit breaker open after {health.get('failure_count')} failures"
    finally:
        if not args.no_compose:
            compose_recreate("llm-gateway", {})


DEMOS = {
    "out-of-corpus": ("Demo 5: out-of-corpus complaint escalates", demo_out_of_corpus),
    "bad-draft": ("Demo 6: verifier rejects a planted citation", demo_bad_draft),
    "provider-outage": ("Demo 8: provider outage degrades to escalation", demo_provider_outage),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("demo", choices=sorted(DEMOS))
    parser.add_argument("--base-url", default=os.getenv("ORCAS_API_URL", "http://localhost:8000"))
    parser.add_argument("--llm-gateway-url", default="http://localhost:8002")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--no-compose", action="store_true",
                        help="do not recreate services; the fault is already configured")
    args = parser.parse_args(argv)
    args.base_url = args.base_url.rstrip("/")
    args.llm_gateway_url = args.llm_gateway_url.rstrip("/")

    title, run_demo = DEMOS[args.demo]
    print(f"=== {title} ===")
    try:
        outcome = run_demo(args)
    except DemoFailure as failure:
        print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"PASS: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
