"""Summarize Locust end-to-end rows and persisted resource deltas."""

import csv
import json
import sys
from pathlib import Path


def end_to_end_row(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        for row in rows:
            if row.get("Name") == "complaint_end_to_end":
                return row
    raise ValueError(f"No complaint_end_to_end row in {path}")


def summarize(directory: Path) -> dict:
    result = {}
    for csv_path in sorted(directory.glob("*_stats.csv")):
        name = csv_path.name.removesuffix("_stats.csv")
        before = json.loads((directory / f"{name}_before.json").read_text())
        after = json.loads((directory / f"{name}_after.json").read_text())
        row = end_to_end_row(csv_path)
        result[name] = {
            "end_to_end": row,
            "locust_exit_code": int((directory / f"{name}.exit_code").read_text()),
            "completed_delta": after["statuses"].get("completed", 0) - before["statuses"].get("completed", 0),
            "escalated_delta": after["statuses"].get("escalated", 0) - before["statuses"].get("escalated", 0),
            "failed_delta": after["statuses"].get("failed", 0) - before["statuses"].get("failed", 0),
            "llm_calls_delta": after["llm_calls"] - before["llm_calls"],
            "tokens_delta": after["tokens"] - before["tokens"],
            "cost_usd_delta": after["cost_usd"] - before["cost_usd"],
            "cache_hits_delta": after["cache_hits"] - before["cache_hits"],
            "queue_depth_after": after["queue_depth"],
        }
    return result


if __name__ == "__main__":
    print(json.dumps(summarize(Path(sys.argv[1])), indent=2, sort_keys=True))
