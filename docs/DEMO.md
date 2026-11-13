# ORCAS demo runbook

Run from the repository root with a synthetic complaint. Set `POSTGRES_PASSWORD`
in `.env`, then start the stack with `docker compose up --build -d`. Use
`docker compose --profile resources up --build -d` when showing container
resource panels. Wait for `python scripts/doctor.py` to pass. The first build
downloads the embedding model and creates the index; prebuild images before
the presentation and use `docker compose up -d` on stage.

The shell scripts below run from Bash or WSL. On Windows, run the same checks
with `python scripts/demo/agent_demos.py out-of-corpus`, `bad-draft` or
`provider-outage`. Items 6 and 8 recreate one service through `docker compose`
and reset a scaled worker to one replica; add `--no-compose` if the fault is
already configured. Use the browser for the web client. Record the run ID from
item 2; use it in item 3.

| # | Lead | Action | Expected evidence | Fallback |
|---|---|---|---|---|
| 1 | Jignas | `python scripts/doctor.py`; open `http://localhost:8080` | Services ready; web client loads | Show `docker compose ps --all` and the CI Compose smoke run while restarting the failed service. |
| 2 | Tahir | Submit the bank fixed-deposit preset in the web client, or run `python scripts/demo/initial_demo.py` | A terminal result with an RBI route and resolvable citations | Use the script and inspect the run through `http://localhost:8000/docs`. |
| 3 | Jignas | `python scripts/demo/show_trace.py <run_id>` | Ordered persisted events and a Jaeger link for that run | Run `python scripts/demo/check_jaeger_trace.py`; show the persisted trace even if Jaeger indexing lags. |
| 4 | Jignas | `python scripts/demo/initial_demo.py`; inspect `GET /v1/admin/quarantine` with the admin token | Malformed dates return `422` and a quarantine ID; no extra run | Use `tests/platform/test_gateway.py` and the CI smoke assertion. |
| 5 | Mitesh | `bash scripts/demo/demo_5_out_of_corpus.sh` | `PASS`: a health-insurance complaint is escalated to a human with regulator `UNDETERMINED` and no citations | Show the recorded synthetic scenario and its run trace. |
| 6 | Mitesh | `bash scripts/demo/demo_6_bad_draft.sh` (recreates the worker with `ORCAS_INJECT_BAD_DRAFT=1`, then restores it) | `PASS`: the verifier rejects the planted citation, the researcher retries with a named strategy, and the fabricated citation is absent from the result | Show the `retry` and second `draft` events with `python scripts/demo/show_trace.py <run_id>`. |
| 7 | Jignas and Mitesh | `python scripts/demo/check_guardrail_rejection.py` | Instruction-like input returns `422` with no queued run | Show the labelled guardrail regression tests. |
| 8 | Mitesh | `bash scripts/demo/kill_provider.sh` (recreates `llm-gateway` with every provider call forced to fail, then restores it) | `PASS`: the run is escalated with `degraded=true`, and the LLM gateway reports its circuit breaker open | Show `http://localhost:8002/health` and the LLM gateway metrics. |
| 9 | Jignas | Compare the [committed benchmark](../loadtest/results/ci/summary.json) with [the unchanged target](slo.md); show `http://localhost:3000` | Ten-client p95 30 s, three-worker p95 11 s, first latency breach at 40 clients (p95 104 s), and queue/resource panels | Use the [CI benchmark](https://github.com/Mitxh13/orcas/actions/runs/43057185087) and state its stub/workload limitations. |
| 10 | Mitesh and Tahir | Submit the bank fixed-deposit preset again with cache bypass | The §4.1 routing basis cites the relevant RBI provision | Open the retrieval and verifier trace for that run. |

Run items 2 and 10 with `X-Run-Cache-Bypass: true` when measuring fresh agent
behavior. Keep the stub mode as a backup for the live-model presentation.
After the model and demo laptop are selected, time two complete rehearsals and
record their dates, durations, failures, and fixes in
[`docs/work/jignas/notes_jignas.md`](work/jignas/notes_jignas.md).
