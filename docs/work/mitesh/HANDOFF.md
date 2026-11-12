# Handoff to Mitesh: end-to-end run and live-model work

Written 2026-11-12 for Mitesh and the coding agent working with him. It assumes no knowledge of
the session that produced the nine improvement slices. Everything below is to be done on a machine
with Docker and at least 8 GB of Docker memory; the machine the slices were written on could not
run the stack.

## 1. Where things stand

**Code:** the nine slices (PRs #32 to #40) are complete. Confirm `main` contains them before you
start: `ls scripts/demo/agent_demos.py prompts/researcher_classify/v3.yaml` must list both files.
If either is missing, the slices are not on `main` yet; stop and tell Tahir.

**Verified so far, all in stub mode:**

| What | Where it was verified |
|---|---|
| 204 unit and contract tests, with the retrieval index built | locally and in CI (`platform` job) |
| Full Compose stack: build, reference case, Jaeger trace, Prometheus and Grafana, guardrail quarantine | CI (`compose-smoke` job) |
| Demos 5, 6 and 8 against the running stack | CI (`compose-smoke` job) |
| Stub evaluation, 25 scenarios: route accuracy 0.72 | `eval/results/agent-stub.json` |

**Never verified, and yours to do:**

1. The stack on a laptop from a clean clone (CI runners are the only place it has run).
2. Anything with a real model. No live-model run exists anywhere in the repository.
3. The two pre-registered prompt experiments.
4. Timed demo rehearsals.

## 2. Ground rules

- Never commit `.env`, API keys or endpoints. `python scripts/check_repository.py` must stay clean.
- The contribution rules also say not to commit model identifiers. Live results files record the
  model name in their `models` field. **Agree as a team whether results files may name the model
  before committing one.** If not, describe the model in the report by size and family only.
- One branch per task (`feat/mitesh/<topic>`), PR into `main`, CI must pass.
- Changes under `contracts/` need an ADR and approval from all three members.
- Do not edit a hypothesis in `eval/experiments.yaml` after results exist. Add a new experiment.
- Report what happened. If a run fails or a number is bad, record it with the output; the
  "what didn't work" sections of the report need exactly that.

## 3. Task A: clean-clone stub run on the laptop

Goal: prove the default stack starts and works outside CI, and measure the first-start time.

```bash
git clone https://github.com/Mitxh13/orcas.git && cd orcas
cp .env.example .env            # then set POSTGRES_PASSWORD to a private local value
time docker compose up --build -d
python scripts/doctor.py        # repeat until it passes
python scripts/demo/initial_demo.py
bash scripts/demo/demo_5_out_of_corpus.sh
bash scripts/demo/demo_6_bad_draft.sh
bash scripts/demo/kill_provider.sh
python scripts/demo/check_guardrail_rejection.py
python scripts/demo/check_observability.py
```

If you have an older checkout or Docker volumes from before 2026-11-12, run
`docker compose down -v` first. The PDF parser pin changed, and an index built with the old pin
has different chunk ids (641 chunks; the correct index has 637 and
`index_version = minilm-l6-v2.clause.637.dd539860ac65`). Check with
`curl -s localhost:8001/health`.

Expected: `initial_demo.py` ends with an RBI route; each demo script prints `PASS:`; each exits 0.

Record in `docs/work/jignas/notes_jignas.md`: machine, Docker memory, first-start duration, and
any failure with its log. Then update the sentence in `README.md` that says the first-start
duration "must be measured on the demo laptop".

Things to know:

- Demos 6 and 8 recreate one service with `docker compose up --force-recreate`. That resets a
  scaled worker to one replica.
- Demo 8 leaves the circuit breaker open in Redis. Stub mode ignores it; in live mode it
  half-opens after `BREAKER_COOLDOWN_S` (30 s).
- The CPU and memory dashboard panels are empty unless the stack is started with
  `docker compose --profile resources up -d`.

## 4. Task B: switch to a live model (Ollama)

1. Choose a model that fits the laptop. Docker Desktop on macOS gives the container no GPU, so a
   small model is the realistic choice there. A native Ollama on the host is an alternative: set
   `LLM_API_BASE=http://host.docker.internal:11434` instead of starting the Compose profile.
2. In `.env`:

   ```
   ORCAS_STUB_MODE=false
   LLM_MODEL=ollama/<model>
   LLM_API_BASE=http://ollama:11434
   ```

3. Start it:

   ```bash
   docker compose --profile ollama up -d ollama
   docker compose exec ollama ollama pull <model>
   docker compose --profile ollama up -d --force-recreate llm-gateway worker
   curl -s localhost:8002/health     # expect "stub_mode": false, "status": "ok"
   ```

4. **Time one call before anything else.**

   ```bash
   time curl -s localhost:8002/v1/chat -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"Reply with the JSON object {\"ok\": true}"}],"agent":"probe","step":"probe"}'
   ```

5. Set the time limits from that measurement. A run makes 4 to 11 model calls. The limits must
   keep this order, and all four must grow together for a slow model:

   | Variable | Default | Meaning |
   |---|---|---|
   | `LLM_TIMEOUT_SECONDS` | 30 | one provider attempt |
   | `LLM_TOTAL_BUDGET_SECONDS` | 90 | all attempts, waits and the fallback for one call |
   | `RUN_WALL_CLOCK_S` | 45 | the researcher's whole loop; exceeded means `budget_exhausted` |
   | `CELERY_SOFT_TIME_LIMIT` / `CELERY_TIME_LIMIT` | 120 / 180 | the whole task |

   With the defaults, a model slower than about 8 s per call will escalate most runs as
   `budget_exhausted`. That is a configuration result, not a model-quality result; raise the
   limits and say in the report what you set. Recreate `llm-gateway` and `worker` after each change.

6. Run the reference case with a fresh run and read the trace:

   ```bash
   python scripts/demo/initial_demo.py --timeout 600
   python scripts/demo/show_trace.py <run_id>
   ```

   `initial_demo.py` requires a completed RBI referral. If the model escalates or routes
   elsewhere, that is a finding: save the trace and go to section 8.

## 5. Task C: live evaluation

The evaluation runs the worker pipeline in-process on the host and sends only the model calls to
the gateway, so it needs a local Python environment and a local index.

```bash
python3.11 -m venv .venv && . .venv/bin/activate
make install-dev
make ingest                      # must report 637 chunks
LLM_GATEWAY_URL=http://localhost:8002 make eval-live
```

- Output: `eval/results/agent-gateway.json`. Open it and check `models`. If it says `llm-stub`,
  the gateway was still in stub mode and the file is mislabeled; delete it.
- Any time-limit variables you raised in `.env` apply to the containers only. Export
  `RUN_WALL_CLOCK_S` and `LLM_TOTAL_BUDGET_SECONDS` in the shell too, because the worker code
  runs in your shell here.
- `make eval-live` sleeps 2 s between scenarios. For a hosted free tier check the daily quota
  first: 25 scenarios is roughly 100 to 275 calls.

Then fill in `docs/work/tahir/evaluation.md` §5 ("Live-model results") and section C of
`docs/work/tahir/report-sections.md` with the table from the new file, next to the stub table.
State the stub and live numbers separately; they measure different things.

Numbers worth looking at first, because stub mode cannot move them:

- `supporting_passage_cited` (0 of 15 in stub mode).
- Whether the reference case cites the RBI preamble or clause 9 and not clause 10. In stub mode
  that preference is coded into the stub; a real model has to get there from the text.
- How many runs end `unverified`, and what the `retry` events say.

## 6. Task D: the two pre-registered experiments

```bash
LLM_GATEWAY_URL=http://localhost:8002 make experiment NAME=l2-reflect-prompt
LLM_GATEWAY_URL=http://localhost:8002 make experiment NAME=l3-cite-by-evidence-number
```

Each runs both configurations on the 25 scenarios (about 200 to 550 calls per experiment) and
writes `eval/results/exp-<name>.json` with a verdict on the primary metric. A difference of one or
two scenarios is flagged as within noise; report it that way.

Then rewrite two sections of your own report, `docs/work/mitesh/report-sections.md`:

- **§6 Prompt Comparison Summary** currently says no comparison has been run. Replace it with the
  measured before and after figures and the verdicts.
- If draft v2 wins on `supporting_passage_cited`, propose making it the default
  (`PROMPT_RESEARCHER_DRAFT` in `docker-compose.yml`, `.env.example` and
  `services/worker/agent/researcher.py`) in a separate PR that cites the results file.

## 7. Task E: cost and tokens

`docs/work/mitesh/report-sections.md` §7 had per-step token shares and a cost per 100 runs that no
run in the repository produced; they were removed. Put real ones back:

1. Set `LLM_PRICE_INPUT_PER_M` and `LLM_PRICE_OUTPUT_PER_M` in `.env` to the list price you are
   using as the equivalent, and recreate `llm-gateway`.
2. Submit a batch through the stack (the load test payloads work:
   `locust -f loadtest/locustfile.py --headless --host http://localhost:8000 --users 2 --spawn-rate 1 --run-time 10m`).
3. Read `orcas_run_tokens_by_step` and `orcas_run_cost_by_step_usd` from
   `http://localhost:9090`, or the "LLM tokens and cost by step" panel in Grafana.
4. Write the share per step and the cost per 100 runs into §7, with the date, the run count and
   the prices used. This also closes the open cost item in
   `docs/work/jignas/report-sections.md`.

## 8. If the live model behaves badly

These are the places a real model is most likely to break, with where to look:

| Symptom | Likely cause | Where |
|---|---|---|
| Most runs `budget_exhausted` | time limits too low for the model | section 4, step 5 |
| Runs `unverified`, retry reason "not retrieved in this run's evidence ledger" | model mis-copies chunk ids with draft v1 | try `PROMPT_RESEARCHER_DRAFT=v2`; this is experiment L3 |
| Runs `unverified`, reason "Quote span ... not found" | model paraphrases instead of quoting | prompt wording in `prompts/researcher_draft/` (add a new version, do not edit one that has results) |
| Model returns prose or fenced JSON | provider ignores JSON mode | `LLMClient.extract_json` already takes the first object; check `LLM_JSON_MODE` |
| Split-regulator complaints are answered instead of escalated | model omits `regulators_involved` | `prompts/researcher_classify/v3.yaml` |
| Verifier accepts everything, or rejects everything | `prompts/verifier/v1.yaml` is thin; it asks only for unsupported claims | add `verifier/v2.yaml` that asks for a `claims` list; the code already reads one |
| Gateway returns 503 with `Retry-After` | provider rate limit longer than the budget | pace with `--sleep`, or use a local model |
| Run stuck in `running` | should no longer happen; any exception now escalates | capture `docker compose logs worker` and open an issue |

Every new prompt is a new version file and a new environment default, never an edit in place once
results exist for the old one.

## 9. Task F: demo rehearsals

After the model and laptop are fixed, time two complete run-throughs of the ten items in
`docs/DEMO.md` and record dates, durations, failures and fixes in
`docs/work/jignas/notes_jignas.md`. Keep stub mode as the fallback: switching back is
`ORCAS_STUB_MODE=true` and `docker compose up -d --force-recreate llm-gateway worker`.

Run items 2 and 10 with `X-Run-Cache-Bypass: true` (the scripts already do), or the second
submission is a cache hit with no agent trace.

## 10. Code work that was deliberately left out

Not needed to run the project, listed in `docs/improvements/MITESH.md`. Take them only if there
is time after sections 3 to 9, each as its own PR with tests:

| Item | What is missing | Starting point |
|---|---|---|
| M5 dynamic planner | The plan step always issues `search_corpus`. `prompts/researcher_plan/v1.yaml` and `parse_action` exist but nothing calls them | `ResearcherGraph.node_plan` in `services/worker/agent/researcher.py`; `services/worker/agent/action_protocol.py` |
| M11 injection isolation | The default reflect, draft v1 and verifier prompts put the complaint in the prompt without `<complaint>` tags, and everything is sent as one user message | prompts; `tests/agent/test_agent_pipeline.py::test_prompt_injection_isolation` uses a fake that ignores the prompt, so it proves nothing yet |
| M17 breaker | Half-open admits all traffic, and the trips counter increments on every exhausted request | `services/llm_gateway/breaker.py` |
| M18 verifier separation | Researcher and verifier share one process and one LLM client. Either separate them or justify it in the report | `services/worker/tasks.py` |
| M15 | `docs/work/mitesh/notes_mitesh.md` does not exist | running log, as the other two members keep |

## 11. Items only people can close

- Approve or reject ADR 0001 and ADR 0002 (`docs/decisions/`); both still say "Proposed" although
  the schema change in 0002 is merged.
- Peer-review the scenario labels: every box in `docs/work/tahir/scenario-review.md` is unticked.
- Tick the boxes in `docs/improvements/MITESH.md`, `TAHIR.md` and `JIGNAS.md` that you have
  checked yourself. None were ticked on your behalf.

## 12. Done means

- [ ] Clean-clone stub run on the laptop recorded, with first-start time.
- [ ] Live model reachable; time limits set from a measured call and written down.
- [ ] `eval/results/agent-gateway.json` committed (subject to the model-name decision in
      section 2), with `models` showing the real model.
- [ ] `eval/results/exp-l2-reflect-prompt.json` and `exp-l3-cite-by-evidence-number.json` committed.
- [ ] Evaluation docs and your report §6 and §7 carry measured numbers, each with its results file.
- [ ] Two timed rehearsals recorded.
- [ ] `python -m pytest tests -q` and `python scripts/check_repository.py` pass on every PR.

Send back to Tahir: the results files, the time limits you ended up with, and every failure from
section 8 with its run id.
