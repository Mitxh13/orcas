#!/usr/bin/env bash
set -euo pipefail

results=loadtest/results/ci
mkdir -p "$results"

snapshot() {
  docker compose exec -T gateway python - < scripts/benchmarks/snapshot.py > "$results/$1.json"
}

run_locust() {
  local name="$1" users="$2" duration="$3" cache_mode="$4"
  snapshot "${name}_before"
  set +e
  LOADTEST_CACHE_MODE="$cache_mode" locust -f loadtest/locustfile.py \
    --headless --host http://localhost:8000 --users "$users" --spawn-rate 2 \
    --run-time "$duration" --stop-timeout 200 --csv "$results/$name" \
    > "$results/$name.log" 2>&1
  local exit_code=$?
  set -e
  echo "$exit_code" > "$results/$name.exit_code"
  snapshot "${name}_after"
  echo "$name: Locust exit code $exit_code"
  tail -n 12 "$results/$name.log"
}

python scripts/demo/initial_demo.py --timeout 180

run_locust baseline_10 10 15m unique
docker compose up -d --scale worker=3 worker
sleep 30
docker compose ps worker
run_locust scaled_10 10 15m unique

docker compose up -d --scale worker=1 worker
sleep 20
run_locust cache_warmup 10 3m repeat
run_locust cache_repeat 10 3m repeat
run_locust cache_bypass 10 3m bypass
run_locust baseline_20 20 5m unique
run_locust baseline_40 40 5m unique

python scripts/benchmarks/summarize.py "$results" | tee "$results/summary.json"
