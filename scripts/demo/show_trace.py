"""Show persisted events and the Jaeger trace for one complaint run."""

import argparse
import json
import time
import urllib.parse
import urllib.request


def read_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--jaeger", default="http://localhost:16686")
    args = parser.parse_args()

    trace = read_json(f"{args.gateway}/v1/runs/{args.run_id}/trace")
    print(f"Run {trace['run_id']} ({trace['status']}), recorded cost ${trace['total_cost_usd']:.4f}")
    for event in trace["events"]:
        print(f"{event['seq']:>3}  {event['ts']}  {event['agent']}/{event['step']}  {event['kind']}")

    query = urllib.parse.urlencode({
        "service": "orcas-worker", "limit": 20, "lookback": "1h",
        "tags": json.dumps({"orcas.run_id": args.run_id}),
    })
    for _ in range(10):
        matches = read_json(f"{args.jaeger}/api/traces?{query}").get("data", [])
        if matches:
            print(f"Jaeger: {args.jaeger}/trace/{matches[0]['traceID']}")
            return 0
        time.sleep(3)
    raise SystemExit("No Jaeger trace for this run yet; check collector and worker logs")


if __name__ == "__main__":
    raise SystemExit(main())
