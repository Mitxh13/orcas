# Incident log

Record only observed development or demo failures. For each entry include UTC
time, affected service, symptom, detection, cause, recovery, and follow-up.

## 2026-10-27 04:27 UTC — Celery task missing in Compose smoke run

- **Affected service:** worker in the stub-backed GitHub Actions Compose stack.
- **Symptom:** a valid complaint stayed queued until the 180-second demo timeout.
  The worker was healthy enough to answer Celery pings but discarded
  `orcas.run_complaint` as an unregistered task.
- **Detection:** the `compose-smoke` job for commit `134fe2f` failed while polling
  run `151e64fd-6b0b-494d-9434-c5292017f141`; worker logs identified the
  unregistered task.
- **Cause:** worker tracing setup imported FastAPI instrumentation although the
  worker image does not install FastAPI. A broad `ImportError` handler then
  suppressed that failure before the task decorator ran.
- **Recovery:** commit `5672df4` loads FastAPI instrumentation only for HTTP
  services and makes unexpected worker import errors fail startup. An isolated
  regression test confirms task registration without FastAPI. The repeat
  [Compose smoke run](https://github.com/Mitxh13/orcas/actions/runs/40450433329)
  passed: run `d3a61367-dc35-4f2f-8639-624f2ba49704` completed with the RBI
  route, and the malformed request returned quarantine ID
  `78a85f7e-b737-4f74-9b7b-1fa5d8fe024e`.
- **Follow-up:** keep the full-stack intake smoke test in CI. The controlled
  worker, Redis, and retrieval checks in the [runbook](runbook.md) later
  passed on a [clean runner](https://github.com/JignasP/orcas/actions/runs/40542611618).

## 2026-10-27 16:35 UTC — Retrieval recovery probe missed a connection reset

- **Affected component:** the controlled failure test's retrieval readiness
  probe on a clean GitHub Actions runner.
- **Symptom and detection:** the first
  [failure-drill run](https://github.com/JignasP/orcas/actions/runs/40540759058)
  failed after restarting retrieval. Its log showed `ConnectionResetError`
  while polling `/ready`; worker and Redis recovery had already passed.
- **Cause:** the probe retried URL errors and timeouts but did not catch a
  connection reset during service startup.
- **Recovery:** commit `22c8f08` retries transient socket errors. The
  [repeat run](https://github.com/JignasP/orcas/actions/runs/40542611618)
  passed worker, Redis, and retrieval recovery.
- **Follow-up:** keep the recovery drill in CI and treat a failed probe as
  test evidence until the service state is checked, rather than assuming the
  application failed to recover.

## 2026-11-03 UTC — Stub routing skewed the measured workload

- **Affected component:** LLM stub and the earlier Locust benchmark.
- **Symptom and detection:** the ten-client phase recorded 101 completed and
  263 escalated runs; the audit found many escalations after one LLM call.
- **Cause:** the stub's narrow parsing and classification did not represent
  the intended multi-step agent workload.
- **Recovery:** commit `3a64fc6` hardened parsing and added a multi-entity
  classifier. The earlier latency table remains historical evidence.
- **Follow-up:** the
  [repeat benchmark](https://github.com/Mitxh13/orcas/actions/runs/43057185087)
  used the same payload mix and stub delay. The ten-client database window
  changed from 101 completed / 263 escalated to 245 completed / 136
  escalated; p95 changed from 40 to 30 seconds. LLM calls per terminal run
  remained near three, so inspect the reference-case trace before claiming
  its full reasoning depth from the aggregate benchmark.

## 2026-11-03 UTC — Bank case cited a non-maintainability clause

- **Affected component:** stub citation selection and agent evidence handling.
- **Symptom and detection:** the audit found that the §4.1 bank case cited RBI
  clause 10 as its routing basis, although that clause covers non-maintainability.
- **Cause:** clause 10 ranked ahead of the decisive grounds clause in the
  retrieved context, and the stub selected it.
- **Recovery:** commit `3a64fc6` excluded non-maintainability headings from
  RBI citation selection; `50dfb54` added verifier ledger checks and `0143493`
  aligned stub citations with an empty evidence ledger.
- **Follow-up:** keep the reference-case citation test in CI and inspect its
  live trace during the demo rehearsal.

## 2026-11-03 UTC — LLM failure could silently use a heuristic answer

- **Affected component:** LLM gateway and worker client.
- **Symptom and detection:** the integration audit found a fallback path that
  could return an unverified heuristic answer when the provider failed.
- **Cause:** provider failure was not represented as a typed unavailable state
  throughout the call chain.
- **Recovery:** commit `8a0b62c` added fail-closed gateway behavior and typed
  `LLMUnavailable` handling.
- **Follow-up:** demonstrate provider failure with `kill_provider.sh` and
  confirm an escalated, degraded determination.

## 2026-11-11 UTC — Retrieval outage drill ended before stale-run recovery

- **Affected component:** CI recovery drill on the stub-backed Compose stack.
- **Symptom and detection:** the first
  [run](https://github.com/Mitxh13/orcas/actions/runs/43057185087) timed out
  while polling a retrieval-outage run after 150 seconds; the earlier worker
  and Redis cases passed.
- **Cause:** the drill's 150-second wait ended before the configured
  180-second worker hard limit plus 60-second stale-run margin.
- **Recovery:** commit `e513503` waits through that recovery window and
  requires an `agent_failure`, degraded escalation. The
  [repeat run](https://github.com/Mitxh13/orcas/actions/runs/43060243324)
  passed all four cases, including a completed new run after retrieval restart.
- **Follow-up:** retain the longer timeout and inspect the persisted run
  status and reason in future recovery checks.
