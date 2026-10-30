"""Verify provisioned dashboard and live Prometheus data after a demo run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request

REQUIRED_JOBS = {"gateway", "retrieval", "llm-gateway", "worker-metrics", "cadvisor"}
REQUIRED_SERIES = {"orcas_gateway_requests_total", "orcas_run_latency_seconds",
                   "orcas_celery_queue_depth", "orcas_eval_score",
                   "orcas_run_tokens_by_step", "orcas_run_cost_by_step_usd"}


def read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def check(prometheus_url: str, grafana_url: str) -> tuple[set[str], int]:
    targets = read_json(f"{prometheus_url}/api/v1/targets?state=active")
    healthy_jobs = {
        target["labels"]["job"] for target in targets["data"]["activeTargets"]
        if target.get("health") == "up"
    }
    missing_jobs = REQUIRED_JOBS - healthy_jobs
    if missing_jobs:
        raise ValueError(f"Prometheus targets are not healthy: {sorted(missing_jobs)}")

    for series in REQUIRED_SERIES:
        query = urllib.parse.urlencode({"query": series})
        result = read_json(f"{prometheus_url}/api/v1/query?{query}")
        if not result.get("data", {}).get("result"):
            raise ValueError(f"Prometheus has no live {series} series")

    dashboard = read_json(f"{grafana_url}/api/dashboards/uid/orcas-platform")["dashboard"]
    if dashboard.get("uid") != "orcas-platform" or len(dashboard.get("panels", [])) < 24:
        raise ValueError("The ORCAS dashboard was not provisioned with its expected panels")
    for panel in dashboard["panels"]:
        if panel["id"] not in {11, 12}:
            continue
        expression = panel["targets"][0]["expr"]
        query = urllib.parse.urlencode({"query": expression})
        result = read_json(f"{prometheus_url}/api/v1/query?{query}")
        if not result.get("data", {}).get("result"):
            raise ValueError(f"Grafana resource panel {panel['id']} has no live data")
    gateway_id = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "gateway"], text=True
    ).strip()
    if not gateway_id:
        raise ValueError("Docker Compose has no running gateway container")
    for series in ("container_cpu_usage_seconds_total", "container_memory_working_set_bytes"):
        query = urllib.parse.urlencode({
            "query": f'{series}{{id=~".*(/docker/|/docker-)[0-9a-f]+([.]scope)?"}}'
        })
        result = read_json(f"{prometheus_url}/api/v1/query?{query}")
        samples = result.get("data", {}).get("result", [])
        if not samples:
            raise ValueError(f"Prometheus has no Docker cgroup {series} series")
        if not any(
            (match := re.search(r"(?:/docker/|/docker-)([0-9a-f]{64})(?:\.scope)?$",
                                sample["metric"].get("id", "")))
            and match.group(1).startswith(gateway_id)
            for sample in samples
        ):
            raise ValueError(f"Docker cgroup {series} has no Compose gateway series")
        print(f"{series}: {len(samples)} Docker cgroup series")
    return healthy_jobs, len(dashboard["panels"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            jobs, panels = check(args.prometheus_url.rstrip("/"), args.grafana_url.rstrip("/"))
            print(f"Live Prometheus targets: {', '.join(sorted(jobs))}; Grafana panels: {panels}")
            return 0
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError) as error:
            last_error = error
        time.sleep(3)
    raise SystemExit(f"Observability verification did not pass: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
