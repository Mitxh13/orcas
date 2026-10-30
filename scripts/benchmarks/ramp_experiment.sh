#!/usr/bin/env bash
set -euo pipefail

results=loadtest/results/ramp
mkdir -p "$results"

snapshot() {
  docker compose exec -T gateway python - < scripts/benchmarks/snapshot.py > "$results/$1.json"
}

run_case() {
  local name="$1" users="$2" duration="$3"
  snapshot "${name}_before"
  local start end
  start=$(date +%s)
  python scripts/benchmarks/docker_stats.py sample "$results/$name.docker-stats.jsonl" &
  local sampler_pid=$!
  set +e
  LOADTEST_CACHE_MODE=unique locust -f loadtest/locustfile.py \
    --headless --host http://localhost:8000 --users "$users" --spawn-rate 2 \
    --run-time "$duration" --stop-timeout 200 --csv "$results/$name" \
    > "$results/$name.log" 2>&1
  local exit_code=$?
  set -e
  kill "$sampler_pid" 2>/dev/null || true
  wait "$sampler_pid" 2>/dev/null || true
  end=$(date +%s)
  echo "$exit_code" > "$results/$name.exit_code"
  snapshot "${name}_after"
  python scripts/benchmarks/prometheus_range.py "$start" "$end" > "$results/$name.metrics.json"
  python scripts/benchmarks/docker_stats.py summarize "$results/$name.docker-stats.jsonl" \
    > "$results/$name.docker-summary.json"
  echo "$name: Locust exit code $exit_code"
  tail -n 12 "$results/$name.log"
}

python scripts/demo/initial_demo.py --timeout 180
run_case ramp_1 1 3m
run_case ramp_5 5 5m
run_case diagnostic_40 40 5m
python scripts/benchmarks/summarize.py "$results" > "$results/summary.json"
