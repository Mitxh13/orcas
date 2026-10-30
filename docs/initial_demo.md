# Initial demo: Jignas platform path

This is the first working path through intake, queue, worker, retrieval, and the
stubbed LLM. It does not cover the final tracing, dashboards, or load-test results.
The LLM stub gives repeatable answers without a provider key; the worker and
retrieval service are real services.

## Start from a clean clone

1. Install Docker Engine/Compose v2 and ensure Docker has enough memory for the
   embedding model and vector index. The initial build downloads images, Python
   packages, and the embedding model.
2. Copy `.env.example` to `.env`. Its default enables the deterministic LLM stub.
3. Run `docker compose up --build -d`. The one-shot `ingest` service builds the
   Chroma index; `migrate` creates the Postgres tables. Wait for `gateway`,
   `worker`, `retrieval`, `llm-gateway`, and `web` to become healthy in
   `docker compose ps`. Initial model download and indexing take longer than a
   normal restart.
4. Open the web client at <http://localhost:8080>. It defaults to the gateway at
   <http://localhost:8000>. The gateway's OpenAPI page is at
   <http://localhost:8000/docs>.

On Windows PowerShell, step 2 is `Copy-Item .env.example .env`. On macOS/Linux,
use `cp .env.example .env`.

## Show the request path

Run `python scripts/demo/initial_demo.py` from the repository root, or submit
the bank-FD preset in the web client. The script submits a valid complaint,
polls until terminal, and then submits a malformed date pair. The malformed
request must return `422` with a `quarantine_id`.

Use the reported quarantine ID to show the persisted rejection:

```sh
docker compose exec -T postgres psql -U orcas -d orcas -c \
  "SELECT quarantine_id, reason, errors FROM quarantine ORDER BY received_at DESC LIMIT 1"
```

To demonstrate that it did not create a run, compare the latest `runs` row and
queue state before and after the malformed request. The automated gateway test
also asserts that rejection creates neither a run nor a dispatch.

```sh
docker compose exec -T postgres psql -U orcas -d orcas -c \
  "SELECT run_id, status, submitted_at FROM runs ORDER BY submitted_at DESC LIMIT 5"
```

## Local checks

With project dependencies installed, run:

```sh
python -m pytest tests/platform tests/agent -q
```

The gateway can be tested without Docker using an in-memory run store and a
recording dispatcher. A passing local test does not prove that Compose starts;
the clean-clone run above is required for that claim.

## Additional platform demo checks

After the intake demo, run `python scripts/demo/check_guardrail_rejection.py`
to show an instruction embedded in a complaint receiving `422` and a
quarantine ID. The Compose smoke job checks that this rejection creates no
additional run. Use `python scripts/demo/check_jaeger_trace.py` to find a
connected gateway, queue, worker, retrieval, and LLM gateway trace in Jaeger.
Run `python scripts/demo/check_observability.py` to check Prometheus scrape
data and the provisioned Grafana dashboard. The benchmark outcomes and
declared target are recorded in [Jignas's report](work/jignas/report-sections.md)
and [the SLO](slo.md).
