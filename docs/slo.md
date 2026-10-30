# Stub-backed complaint processing target

Declared before the first ORCAS end-to-end load test. This is a test target,
not a claim that the system already meets it.

## Workload

- Start from a populated retrieval index and a healthy Compose stack.
- Use the deterministic LLM stub with `STUB_DELAY_MS=1500`; no provider calls.
- Run one worker container with two Celery processes for the baseline.
- Simulate ten concurrent clients. Each submits one synthetic complaint from
  `loadtest/payloads.json` and polls every second until `completed`,
  `escalated`, or `failed`.
- Exclude the two labelled prompt-injection payloads from the processing
  latency mix; test their expected `422` responses separately.
- Measure elapsed time from starting the POST to seeing a terminal response.
  Do not substitute gateway POST latency for this measurement.
- Run at least 100 complete complaints after warmup. Report p50, p95, completed
  runs per second, terminal failure rate, and the number of attempted runs.

## Target

At ten concurrent clients, p95 end-to-end latency should be **at most 90
seconds**, and at least **99%** of accepted runs should reach `completed` or
`escalated`. This allows for queued multi-step analysis while keeping a
reviewer-facing workflow under roughly a minute and a half for most requests.
The worker has a 90-second per-task limit; queue time is included in the SLO.

Find the first higher concurrency where either target fails. Repeat the same
payload mix and stub delay with `docker compose up --scale worker=3`. Compare
both runs' p50, p95, throughput, queue depth, CPU, and database activity.
Record the limiting component. If scaling does not help, report that result
and the observed bottleneck.

The stub returns zero provider cost. Any production cost estimate must use
persisted LLM accounting events and be labelled separately from this test.
