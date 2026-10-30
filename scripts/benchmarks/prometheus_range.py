"""Capture queue and container resource samples for one load-test interval."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
import urllib.request


def query_range(base_url: str, expression: str, start: int, end: int) -> list[dict]:
    query = urllib.parse.urlencode({"query": expression, "start": start, "end": end, "step": 15})
    with urllib.request.urlopen(f"{base_url}/api/v1/query_range?{query}", timeout=15) as response:
        body = json.load(response)
    if body.get("status") != "success":
        raise ValueError(f"Prometheus query failed: {expression}: {body}")
    return body["data"]["result"]


def values(series: list[dict]) -> list[float]:
    return [float(value) for item in series for _, value in item.get("values", [])
            if value not in {"NaN", "+Inf", "-Inf"}]


def compose_container_ids() -> dict[str, str]:
    """Map Docker IDs to Compose services without requiring daemon access in cAdvisor."""
    services = subprocess.check_output(
        ["docker", "compose", "config", "--services"], text=True
    ).splitlines()
    containers = {}
    for service in services:
        ids = subprocess.check_output(
            ["docker", "compose", "ps", "-q", service], text=True
        ).splitlines()
        for index, container_id in enumerate(ids, start=1):
            containers[container_id] = f"{service}-{index}" if len(ids) > 1 else service
    return containers


def resource_max_by_container(series: list[dict], container_ids: dict[str, str]) -> dict[str, float]:
    maxima = {}
    for item in series:
        match = re.search(r"(?:/docker/|/docker-)([0-9a-f]{64})(?:\.scope)?$",
                          item["metric"].get("id", ""))
        if not match:
            continue
        name = next((service for container_id, service in container_ids.items()
                     if match.group(1).startswith(container_id)), None)
        if name is None:
            continue
        samples = values([item])
        if samples:
            maxima[name] = max(maxima.get(name, 0.0), max(samples))
    return maxima


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    args = parser.parse_args()
    base = args.prometheus_url.rstrip("/")
    queue = values(query_range(base, "orcas_celery_queue_depth", args.start, args.end))
    container_ids = compose_container_ids()
    cpu = query_range(base, "sum by (id) (rate(container_cpu_usage_seconds_total[1m]))",
                      args.start, args.end)
    memory = query_range(base, "container_memory_working_set_bytes", args.start, args.end)
    print(json.dumps({
        "start_unix": args.start,
        "end_unix": args.end,
        "queue_depth_max": max(queue) if queue else None,
        "queue_depth_mean": sum(queue) / len(queue) if queue else None,
        "queue_samples": len(queue),
        "compose_container_count": len(container_ids),
        "cadvisor_cpu_series": len(cpu),
        "cadvisor_memory_series": len(memory),
        "cadvisor_id_examples": [item["metric"].get("id", "") for item in memory
                                 if "docker" in item["metric"].get("id", "")][:5],
        "cpu_cores_max_by_container": resource_max_by_container(cpu, container_ids),
        "memory_bytes_max_by_container": resource_max_by_container(memory, container_ids),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
