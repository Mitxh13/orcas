# Complaint intake contract

`contracts/complaint.v1.schema.json` is the frozen request schema for `POST /v1/complaints`.
The gateway's Pydantic model tracks its properties and required fields; the additional
date and cross-field rules below are enforced before a run is queued.

| Field | Rule |
|---|---|
| `client_id` | Required, 3–64 ASCII letters, digits, `_`, or `-` |
| `is_synthetic` | Required and exactly `true` |
| `channel` | Required; `web`, `email`, `phone`, or `branch` |
| `text` | Required, 30–4000 printable characters; no control characters |
| `date_of_event` | Required ISO date, no later than today |
| `date_filed` | Required ISO date, on or after `date_of_event`, no later than today |
| `amount_disputed_inr` | Optional number from 0 through 1,000,000,000 |
| `entity_name_hint` | Optional printable string of at most 120 characters |
| `prior_complaint_to_entity` | Optional boolean |
| `prior_complaint_date` | Required when `prior_complaint_to_entity=true`; then it must fall between event and filing dates. |

Unknown fields and wrong JSON types are rejected. A valid request returns HTTP `202` with
`run_id` and `status_url`; clients poll `GET /v1/runs/{run_id}` for `queued`, `running`,
`completed`, `escalated`, or `failed`. The run status response follows
`contracts/run_status.v1.schema.json`.

Malformed JSON or invalid input returns HTTP `422` with field-level `detail` entries and a
`quarantine_id`. The raw submitted body and validation errors are persisted to the
`quarantine` table. **No `runs` row or Celery task is created for a rejected request.**
The quarantine ID lets the demo locate the rejection without exposing all rejected
complaint text through a public endpoint.

The gateway screens `text` and `entity_name_hint` for explicit instructions to the
agent and threats after schema validation and before creating a run. Flagged requests
also return `422` and are quarantined with `guardrail:injection` or
`guardrail:toxicity`. The current labelled set contains 20 attack examples and 20
benign complaints; 0 of the 20 benign examples were flagged. This small set is a
regression check, not an estimate of production false-positive rate.

`GET /v1/runs/{run_id}/trace` returns ordered persisted events and their total
recorded cost. `POST /v1/runs/{run_id}/feedback` accepts `{"rating":"up|down",
"reason":"..."}` and persists a rating for an existing run. Quarantine listing
and feedback totals are available at `GET /v1/admin/quarantine` and
`GET /v1/admin/feedback/summary`. Both admin endpoints require
`Authorization: Bearer <ORCAS_ADMIN_TOKEN>` and are unavailable until that
environment variable is configured.

Valid submissions are limited by a Redis token bucket for each `client_id`.
The default capacity is 60 requests per minute. A rejected request returns
`429` with `Retry-After` and creates no run. The gateway exposes request
latency, status counts, and rate-limit rejections at `GET /metrics`.

Completed determinations may be reused for an identical complaint from the
same client when the retrieval index and prompt files are unchanged. The
gateway returns a fresh run ID that is immediately `completed`, with
`X-Run-Cache: hit`; no Celery message is sent. The cache TTL defaults to one
hour. `X-Run-Cache-Bypass: true` forces a fresh execution for evaluation.
If the cache or index-info service is unavailable, the gateway queues a fresh
run. Cache hit and miss counts are exposed on `/metrics`.

The gateway's `/docs` page shows the request and response shapes. Changes to the frozen
schema require the decision and approval process in `contracts/README.md`.
