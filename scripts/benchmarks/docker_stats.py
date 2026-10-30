"""Sample Docker CLI CPU and memory for running Compose containers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3,
         "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def memory_bytes(usage: str) -> float:
    match = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", usage.split("/")[0].strip())
    if not match or match.group(2) not in UNITS:
        raise ValueError(f"Unsupported Docker memory usage: {usage}")
    return float(match.group(1)) * UNITS[match.group(2)]


def compose_names() -> dict[str, str]:
    output = subprocess.check_output(["docker", "compose", "ps", "--format", "json"], text=True)
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {row["Name"]: row["Service"] for row in rows if row.get("State") == "running"}


def sample(path: Path, interval: int) -> None:
    with path.open("w", encoding="utf-8") as result:
        while True:
            names = compose_names()
            if not names:
                raise ValueError("No running Compose containers to sample")
            output = subprocess.check_output(
                ["docker", "stats", "--no-stream", "--format", "{{ json . }}", *names],
                text=True, timeout=30,
            )
            timestamp = time.time()
            for line in output.splitlines():
                row = json.loads(line)
                if row.get("Name") not in names:
                    continue
                result.write(json.dumps({
                    "timestamp": timestamp,
                    "service": names[row["Name"]],
                    "container": row["Name"],
                    "cpu_percent": float(row["CPUPerc"].rstrip("%")),
                    "memory_bytes": memory_bytes(row["MemUsage"]),
                }) + "\n")
            result.flush()
            time.sleep(interval)


def summarize(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not rows:
        raise ValueError("No Docker stats samples captured")
    cpu = {}
    memory = {}
    for row in rows:
        name = row["container"]
        cpu[name] = max(cpu.get(name, 0.0), row["cpu_percent"])
        memory[name] = max(memory.get(name, 0.0), row["memory_bytes"])
    return {"samples": len(rows), "cpu_percent_max_by_container": cpu,
            "memory_bytes_max_by_container": memory}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("sample", "summarize"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if args.mode == "sample":
        sample(args.path, args.interval)
    else:
        print(json.dumps(summarize(args.path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
