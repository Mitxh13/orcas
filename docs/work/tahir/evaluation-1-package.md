# Evaluation 1 Package — Data, Retrieval & Quality (Tahir)

**Date:** 2026-10-13 · **Owner:** Tahir · **Target:** ≤ Day 13 (confirm the real Evaluation 1 date with faculty and shift if needed).

This package confirms the corpus, pins the passages the §4.1 reference case depends on, states the reference answer and test values, and gives the project plan and current status. It is reproducible from the repo at the `index_version` reported by the retrieval service.

---

## 1. Corpus confirmation

Pinned in [`corpus/MANIFEST.yaml`](../../../corpus/MANIFEST.yaml) with real SHA-256 and text-layer flags. All three documents have a text layer (no OCR needed) and are indexed.

| doc_id | Title | Issuer | Version | Pages | SHA-256 (short) | Text layer |
|---|---|---|---|---|---|---|
| `sebi_mutual_funds_regulations_1996` | SEBI (Mutual Funds) Regulations, 1996 | SEBI | Consolidated to Jan 2026 | 162 | `79ca5ef1…230043` | yes |
| `rbi_integrated_ombudsman_scheme_2021` | Reserve Bank – Integrated Ombudsman Scheme, 2021 | RBI | Base 2021 (notified 12-Nov-2021) | 18 | `a354a73b…d69a3e` | yes |
| `sebi_scores_master_circular` | Master Circular on redressal of investor grievances through SCORES | SEBI | `SEBI/HO/OIAE/IGRD/P/CIR/2022/0150`, 07-Nov-2022 | 11 | `78a729d8…25f8b` | yes |

**F3 entity coverage — confirmed.** The SCORES master circular is addressed to listed companies, registered intermediaries (brokers, depository participants, mutual funds/AMCs, **registrars/RTAs**), stock exchanges and depositories. A single SEBI grievance document therefore covers the registrar / listed-company / depository categories; **no extra SEBI source is needed for v0.**

**Supersession noted (honest caveat).** The 2022 SCORES master circular was later superseded by SCORES 2.0 (circular `SEBI/HO/OIAE/IGRD/CIR/P/2023/156`, 20-Sep-2023), which changed operational **timelines** (e.g. the redressal window was cut to 21 days). We index the 2022 master circular because it is a clean, self-contained consolidated circular, sufficient for the §4.1 jurisdiction reasoning. Timeline-sensitive eval labels (Slice 5) will be checked against 2023/156 before use; citations always resolve to text that is in the corpus.

**Index:** 606 chunks (SEBI MF 539, RBI 39, SEBI SCORES 28), embedding model `all-MiniLM-L6-v2` (`model_tag=minilm-l6-v2`), cosine space. The live `index_version` is reported by `GET /v1/index/info`.

---

## 2. The §4.1 reference case

> *"I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI."*

**Required reasoning chain:** a naive single search returns the SEBI grievance framework (appears to apply) → the agent extracts *product = bank fixed deposit, entity = bank* → it searches again, framework-filtered to **RBI** → it finds the RBI Integrated Ombudsman Scheme provisions covering RBI-regulated entities (banks) and the grounds of complaint → it drafts **refer_to_other_regulator → RBI** → the verifier confirms every claim and citation.

### 2.1 Apparent passage (what a single SEBI search surfaces)
- **doc:** `sebi_scores_master_circular` · **clause_id:** `p1w1` · **chunk_id:** `ae8861c58c329fc7062bef1db8c34b7cf6efbf1398ed39367f0efd2815d42d7f`
- **quote:** "…registered intermediaries and recognised MIIs that such investor grievances may be resolved faster if these grievances are taken up directly with the entity concerned at the first instance…"
- **Role:** establishes SEBI's SCORES scope (securities-market entities). It does **not** cover a bank deposit — which is why a single pass would misroute.

### 2.2 Deciding passages (the framework swap to RBI)
**(a) Scope / applicability — who the RBI scheme covers**
- **doc:** `rbi_integrated_ombudsman_scheme_2021` · **clause_id:** `p1w0` · **chunk_id:** `801da99662534d0c3a0f0e8cd188d1394aee534a498b2a08a140cfa5ea9faf91`
- **quote:** "…regulated by Reserve Bank of India in an expeditious and cost-effective manner under Section 35A of the Banking Regulation Act, 1949 (10 of 1949), Section 45L of the Reserve Bank of India Act, 1934…"
- **Role:** the scheme applies to services provided by **entities regulated by the Reserve Bank** — i.e. banks. A bank FD falls here.

**(b) Grounds of complaint — the deciding provision**
- **doc:** `rbi_integrated_ombudsman_scheme_2021` · **clause_id:** `p6w0` · **chunk_id:** `7b4481e134cbf5696953bd16b43861b2c2fe762584e280c55423ab1a7a0fb53e`
- **clause text:** "9. Grounds of Complaint — Any customer aggrieved by an act or omission of a Regulated Entity resulting in **deficiency in service** may file a complaint under the Scheme…"
- **Role:** gives the concrete ground on which the bank-FD complaint is maintainable under RBI.

All three quotes are exact substrings of their chunks (verified in `tests/retrieval/test_recall.py`), so every citation resolves via `GET /v1/chunks/{chunk_id}?quote_span=…`.

### 2.3 Reference answer / test values (`determination.v1`)
```json
{
  "status": "completed",
  "route": "refer_to_other_regulator",
  "regulator": "RBI",
  "entity_type": "bank",
  "basis": "The complaint concerns a bank fixed deposit (a banking product), not a securities-market product. SEBI's SCORES mechanism covers listed companies and SEBI-registered intermediaries [c1], not banking deposit services. The RBI Integrated Ombudsman Scheme applies to services provided by entities regulated by the Reserve Bank [c2] and provides grounds of complaint for deficiency in service by a Regulated Entity [c3]. The complaint is therefore referred to the RBI Integrated Ombudsman Scheme.",
  "citations": [
    {"citation_id": "c1", "doc_id": "sebi_scores_master_circular",            "clause": "p1w1", "chunk_id": "ae8861c5…2d7f"},
    {"citation_id": "c2", "doc_id": "rbi_integrated_ombudsman_scheme_2021",   "clause": "p1w0", "chunk_id": "801da996…af91"},
    {"citation_id": "c3", "doc_id": "rbi_integrated_ombudsman_scheme_2021",   "clause": "p6w0", "chunk_id": "7b4481e1…b53e"}
  ],
  "escalation": null,
  "verification": {"passed": true},
  "disposed": false
}
```
Expected labels for the harness: `expected_route=refer_to_other_regulator`, `expected_regulator=RBI`, `expected_entity_type=bank`, `expected_escalation=false`. These, plus 8 other scenarios, are in [`tests/fixtures/retrieval_fixtures.json`](../../../tests/fixtures/retrieval_fixtures.json) (delivered to Mitesh).

---

## 3. How retrieval proves it
- `POST /v1/search` with `filters.framework=RBI` on a deciding query surfaces the scope (`p1w0`) and grounds (`p6w0`) passages in the top-5; the SEBI-filtered search surfaces the apparent passage. Asserted in `tests/retrieval/test_recall.py` (16+ tests passing).
- `GET /v1/chunks/{chunk_id}?quote_span=…` returns `quote_span_found=true` for each citation above — citations resolve to retrieved text.
- `GET /v1/index/info` reports build time, per-document versions, chunk counts and `index_version` (freshness, D2); `GET /metrics` exposes `orcas_index_chunks`, `orcas_index_built_timestamp`, latency and results-per-query.

---

## 4. Project plan and status

Full slice-by-slice plan: [`build-plan.md`](build-plan.md). Status against it:

| Slice | Scope | Status |
|---|---|---|
| 0 | Retrieval contract + pinned corpus | ✅ merged (PR #2) |
| 1 | Ingest v0 + retrieval v0 + §4.1 fixtures | ✅ merged (PR #3) |
| 2 | **This package** + retrieval containerized (model baked, `/health`, `/metrics`) | ✅ this PR |
| 3 | Web client v0 (submit/poll/display) | next, joins the Day-14 walking skeleton |
| 4 | Structure-aware chunking, scope cards, metadata, hybrid A/B, re-embed, freshness | Days 15–22 |
| 5 | ≥20 scenarios, `run_eval.py`, metrics, TruLens, baseline | Days 23–30 (baseline = hard milestone) |
| 6 | Deliberate-change before/after, prompt variants, retrieval cache, live scorer | Days 31–36 |
| 7 | Web client finish + retrieval hardening | Days 37–40 |

**Containerization (this slice):** `services/retrieval/Dockerfile` and `services/ingest/Dockerfile` bake the MiniLM model at build time (no runtime download); retrieval reads the Chroma index from the `/data` volume the ingest one-shot writes. `/health` returns 503 until the index is populated, then 200. Build/run commands are in `services/retrieval/README.md`. (Image not built in this environment — Docker daemon offline, limited disk; build on a Docker host or CI.)

---

## 5. Reproduce
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m services.ingest                       # build the index (~18s, idempotent)
uvicorn services.retrieval.app:app --port 8001   # serve
python -m pytest tests/retrieval -q              # 18 passing (incl. §4.1 recall + citation resolution)
```

## 6. Open items for faculty / team
1. Confirm the real Evaluation 1 date (plan assumes ≤ Day 13).
2. Agree the §4.1 reference answer above as the graded reference (clause ids/quotes are pinned from the downloaded documents, not memory).
3. Timeline supersession (2022 vs SCORES 2.0) — acknowledged; handled at eval-labelling time.

---

## Addendum (2026-11-03): the current §4.1 reference answer

Slice 4 replaced the naive positional chunks (`p1w1`, `p1w0`, `p6w0` in §2) with clause-aligned
chunks, so those IDs no longer exist. **This addendum is the current reference answer**; §2 above is
kept as the point-in-time Evaluation 1 record. The pinned passages are checked by
`tests/eval/test_reference_case.py`, which fails if a re-ingest changes them.

**Index:** 637 chunks, clause strategy. Since incremental ingest (T8, #24) `index_version` is derived from
the chunk ids: `minilm-l6-v2.clause.637.dd539860ac65`. Builds before that used a timestamped string for the
same chunks (`minilm-l6-v2.clause.20261013T074855Z.637`). The chunk ids below are content hashes and do not
change either way.

### Pinned passages

| Role | Document | Clause | chunk_id | Exact quote |
|---|---|---|---|---|
| Apparent (SEBI scope) | `sebi_scores_master_circular` (`SEBI/HO/OIAE/IGRD/P/CIR/2022/0150`) | `2` | `d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e` | "registered intermediaries and recognised MIIs that such investor grievances may be resolved faster if these grievances are taken up directly with the entity concerned at the first instance" |
| Deciding: who the RBI scheme covers | `rbi_integrated_ombudsman_scheme_2021` | `preamble` | `c3cdfa20cf3464b2e00edbf53c1392dffdd8a237837b12f092cc4bdfa3bcc2b0` | "A Scheme for resolving customer grievances in relation to services provided by entities regulated by Reserve Bank of India" |
| Deciding: ground of complaint | `rbi_integrated_ombudsman_scheme_2021` | `9` | `17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64` | "Any customer aggrieved by an act or omission of a Regulated Entity resulting in deficiency in service may file a complaint under the Scheme" |
| **Must not be cited as the basis** | `rbi_integrated_ombudsman_scheme_2021` | `10` | `b4589d638e980d6df903edf684a3a7842dbacbca8bba6d8dd6fdf9788b1249d6` | "Grounds for non-maintainability of a Complaint": lists matters where a complaint does *not* lie |

All three pinned quotes return `quote_span_found=true` from `GET /v1/chunks/{chunk_id}?quote_span=…`.

### Reference determination (`determination.v1`)

```json
{
  "status": "completed",
  "route": "refer_to_other_regulator",
  "regulator": "RBI",
  "entity_type": "bank",
  "timeline": null,
  "basis": "The complaint concerns a fixed deposit opened through a bank, which is a banking product rather than a securities-market product. SEBI's SCORES mechanism is addressed to listed companies, registered intermediaries and recognised MIIs [c1]. The RBI Integrated Ombudsman Scheme covers services provided by entities regulated by the Reserve Bank [c2], and a customer aggrieved by a Regulated Entity's deficiency in service may complain under it [c3]. The complaint is therefore referred to the RBI Integrated Ombudsman Scheme.",
  "citations": [
    {"citation_id": "c1", "doc_id": "sebi_scores_master_circular", "clause": "2", "chunk_id": "d2d66258e59fc24f14530d30125912f54bd3c045f20e9a915253e947e682323e"},
    {"citation_id": "c2", "doc_id": "rbi_integrated_ombudsman_scheme_2021", "clause": "preamble", "chunk_id": "c3cdfa20cf3464b2e00edbf53c1392dffdd8a237837b12f092cc4bdfa3bcc2b0"},
    {"citation_id": "c3", "doc_id": "rbi_integrated_ombudsman_scheme_2021", "clause": "9", "chunk_id": "17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64"}
  ],
  "escalation": null,
  "verification": {"passed": true},
  "disposed": false
}
```

The SEBI citation is optional (it explains why SEBI does not apply); the determination must cite
the RBI preamble and/or clause 9, and must not cite clause 10 as its basis. Harness labels are
unchanged: `refer_to_other_regulator`, `RBI`, `bank`, no escalation.

### Status of the running system (stub mode)

As of 2026-11-12 the run starts in the SEBI framework (the complainant names SEBI), swaps to RBI
after reflection, routes to RBI and cites the **preamble** with a quote that resolves in the
retrieved text. `test_reference_case.py` checks this without an expected-failure marker. Two limits
remain: clause 9 is not among the top five passages the agent's query returns, so it is never
cited; and the preference for the deciding clause lives in the stub's citation chooser, so a live
model has to be checked separately. Earlier builds cited clause 10 with a quote that started
mid-word.

### Demo #10 narration

1. **What a single search returns.** Search the complaint text with no filter: the top hit is SEBI
   SCORES clause 3 (filing complaints on SCORES, score 0.61). The complaint says "complain to SEBI"
   and uses investment vocabulary, so a single embedding search lands on SEBI's framework.
2. **What SEBI's framework actually covers.** SEBI-filtered, the scope passage is SCORES clause 2:
   it is addressed to listed companies, registered intermediaries and recognised MIIs. A bank is
   none of these, and a bank fixed deposit is not a securities product. (The SEBI scope card is a
   finding aid that points to this clause; it is never cited.)
3. **Why one search is not enough.** RBI-filtered, the complaint text alone does not surface the
   deciding provisions: neither the preamble nor clause 9 is in the top 5. The deciding provision
   uses words the complaint never uses. Searching again with vocabulary from the scope passages
   ("services provided by entities regulated by the Reserve Bank", "deficiency in service") brings
   up clause 9 (0.656), and the RBI scope card points to the preamble.
4. **The decision.** The preamble says the scheme covers services of RBI-regulated entities (a
   bank is one); clause 9 says a customer aggrieved by a Regulated Entity's deficiency in service
   may complain (lower interest than promised is a service deficiency). So: refer to the RBI
   Integrated Ombudsman Scheme.
5. **The trap to point out.** For those same queries clause 10 ranks first (0.72), because it
   repeats "deficiency in service" and "Regulated Entity". It lists where complaints do *not* lie,
   so citing it as the basis for a referral contradicts the answer. The verifier must reject it.

Commands to show the passages live (retrieval is published on port 8001):

```bash
curl -s localhost:8001/v1/search -H 'content-type: application/json' \
  -d '{"query": "I invested in a fixed deposit through my bank'\''s app. The interest credited is lower than what I was promised. I want to complain to SEBI.", "k": 5}'
curl -s localhost:8001/v1/search -H 'content-type: application/json' \
  -d '{"query": "services provided by entities regulated by the Reserve Bank deficiency in service grounds of complaint", "k": 5, "filters": {"framework": "RBI"}}'
curl -s "localhost:8001/v1/chunks/17192ff2b4575e221099d33358788a939ad2059d5b9617db56f157b82b726a64?quote_span=Any%20customer%20aggrieved"
```
