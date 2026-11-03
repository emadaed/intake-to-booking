# SPEC-001 — Lead deduplication (idempotent lead creation)

**Status:** Proposed · **Priority:** P1 · **Backlog:** B-06 · **Owner:** Engineering · **Author:** Ahmad · **Date:** 2026-11-03

## 1. Context
`POST /leads` (src/lead_api.py) inserts a new row into `fact_lead` every time it is called. There is no unique
identifier from the source, so if the same submission is sent twice (for example an n8n retry after a lost
response, or a form double-submit), two leads are created and every downstream count and cost-per-lead is wrong.

## 2. Requirements
1. The caller supplies an optional `external_ref` (string, 1–64 chars) that identifies the submission at its source.
2. `fact_lead` gets a nullable `external_ref` column with a **unique index** (nulls allowed, many).
3. If `external_ref` is present and already exists with the same payload, the API returns **200** with the original `lead_id` and does **not** insert (idempotent success).
4. If `external_ref` exists but the payload differs (source, campaign, payer type), return **409** with the existing `lead_id`; nothing is changed.
5. If `external_ref` is absent, behavior is unchanged (insert) and the response includes `"deduplicated": false, "dedup_key_missing": true` so callers can be fixed.
6. The check and insert must be safe under concurrent requests with the same key (rely on the unique index, not on a prior SELECT).

## 3. Out of scope
- Recognizing the same *person* across different submissions (needs contact data this system deliberately does not store).
- Merging or deleting existing duplicates.

## 4. Interface
Request body adds one optional field: `external_ref`. Response adds `deduplicated` (boolean).
`/rejected-leads` is unchanged.

## 5. Data and migration
- Add column `external_ref TEXT` and `CREATE UNIQUE INDEX idx_lead_external_ref ON fact_lead(external_ref)`.
- Existing rows keep `NULL`. No backfill.
- SQLite cannot add a UNIQUE constraint with `ALTER TABLE ADD COLUMN`; use the separate unique index.
- `src/sync_to_postgres.py` and `schema.sql` must be updated to include the column.

## 6. Edge cases
| Case | Expected |
|------|----------|
| Same `external_ref`, same payload, sent twice | 200, one row |
| Same `external_ref`, different payload | 409, original row unchanged |
| Two identical requests at the same instant | exactly one row; the second gets 200 |
| `external_ref` empty string or over 64 chars | 422 `invalid_external_ref` |
| `external_ref` containing contact details (for example an email) | cannot be detected by the API; the **caller** must send an opaque ID (see open questions) |
| Missing `external_ref` | inserts as today, flagged in the response |

## 7. Acceptance criteria
- **Given** a lead with `external_ref=A` exists, **when** the same lead is posted again, **then** the API returns 200, the same `lead_id`, and `fact_lead` row count is unchanged.
- **Given** `external_ref=A` exists, **when** a lead with `external_ref=A` but a different `utm_source` is posted, **then** the API returns 409 and the stored row is unchanged.
- **Given** 20 simultaneous posts with the same new `external_ref`, **then** exactly one row exists afterward.
- **Given** a post without `external_ref`, **then** the lead is stored and the response contains `dedup_key_missing: true`.

## 8. Test plan
Unit tests for each row of the edge-case table; one concurrency test (threads) against a temporary database; migration test on a copy of an existing database; regression run of the existing 21 tests.

## 9. Open questions
1. What value will the source provide as `external_ref` (form-provider submission ID, CRM record ID, n8n execution ID)? Execution IDs change on a manual re-run, so they may not be suitable.
2. Should a duplicate within a time window (for example 10 minutes) with no key be blocked, or only keyed duplicates?
3. Is a 409 for a conflicting payload acceptable to callers, or should it be logged and treated as success?
