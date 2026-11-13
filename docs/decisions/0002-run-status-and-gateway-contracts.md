# ADR 0002 — Run status nullability and gateway contracts

- **Status:** Proposed; requires approval from Mitesh, Tahir, and Jignas before merge
- **Date:** 2026-11-11
- **Owner:** Jignas

## Context

The frozen run status schema combines `type: ["object", "null"]` with a `$ref` to the
determination schema. The reference rejects `null`, although queued and running
responses have no result. The trace and feedback endpoints also lack JSON Schemas.

## Decision

Allow `null` or a determination through `anyOf` in `run_status.v1.schema.json`.
Add `trace.v1.schema.json` and `feedback.v1.schema.json` for the current gateway
responses and feedback request. Keep endpoint paths and runtime behavior intact.

## Consequences

Queued and running responses validate against the contract. CI validates real
gateway responses and rejects future drift. This ADR and the schema changes
must receive the three contract approvals before merging.
