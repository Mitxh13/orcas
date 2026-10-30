"""Confirm one recent request has a connected trace across the pipeline."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

REQUIRED_SERVICES = {
    "orcas-gateway",
    "orcas-worker",
    "orcas-retrieval",
    "orcas-llm-gateway",
}


def matching_trace(base_url: str) -> tuple[str, set[str]] | None:
    query = urllib.parse.urlencode({"service": "orcas-gateway", "limit": 50, "lookback": "1h"})
    with urllib.request.urlopen(f"{base_url}/api/traces?{query}", timeout=10) as response:
        traces = json.load(response).get("data", [])
    for trace in traces:
        processes = trace.get("processes", {})
        service_by_process = {
            process_id: process.get("serviceName")
            for process_id, process in processes.items()
        }
        spans = trace.get("spans", [])
        present = {service_by_process.get(span.get("processID")) for span in spans}
        publish_ids = {
            span.get("spanID") for span in spans
            if span.get("operationName") == "queue.publish"
            and service_by_process.get(span.get("processID")) == "orcas-gateway"
        }
        worker_continues_publish = any(
            span.get("operationName") == "worker.run_complaint"
            and service_by_process.get(span.get("processID")) == "orcas-worker"
            and any(ref.get("spanID") in publish_ids for ref in span.get("references", []))
            for span in spans
        )
        if REQUIRED_SERVICES <= present and worker_continues_publish:
            return trace["traceID"], present
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:16686")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            match = matching_trace(args.base_url.rstrip("/"))
            if match:
                trace_id, services = match
                print(f"Jaeger trace {trace_id}: {', '.join(sorted(services))}")
                return 0
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(3)
    raise SystemExit(f"No trace includes all required services: {sorted(REQUIRED_SERVICES)}; "
                     f"last query error: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
