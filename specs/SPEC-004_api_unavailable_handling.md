# SPEC-004 — Resilient lead intake when the API is unavailable

**Status:** Proposed · **Priority:** P1 · **Backlog:** B-07 · **Depends on:** SPEC-001 · **Owner:** Engineering · **Author:** Ahmad · **Date:** 2026-11-03

## 1. Context (what was observed)
In the n8n workflow, the HTTP Request node that calls `POST /leads` has **Never Error** switched on, so that API
answers such as 422 can be routed to a "rejected" branch.

During testing the API was stopped, and the node failed with `ECONNREFUSED`: there was no response to route, so
the lead was **lost** (it exists only in n8n's execution history). Three weaknesses follow:

1. **Never Error does not cover "no answer".** It only helps when the API replies with an error code.
2. **Validation failures and outages look the same.** The IF node treats "not 201" as one failure path, so a 500 (retry would help) is logged like a 422 (retry would not).
3. **The dead-letter path depends on the same API.** Rejected leads are logged by calling `POST /rejected-leads`; if the API is down, that write fails too.

## 2. Requirements
1. **No accepted lead is lost** when the API is down for up to 24 hours (value to be confirmed).
2. Before calling the API, the raw lead is written to a **durable inbox** that does not depend on the API (for example a queue or a separate store).
3. Failures are classified: **permanent** (4xx validation: do not retry, log to `rejected_lead`), **transient** (5xx, timeout, connection refused: retry), **unknown** (treat as transient).
4. Transient failures are retried with increasing wait times and a retry limit; after the limit, the lead goes to a **failed-delivery** store for manual action.
5. Retries are only safe once SPEC-001 exists, because a timeout can mean the API inserted the lead but the reply was lost.
6. An alert is sent when the oldest undelivered lead is older than a set age, or when failed-delivery is non-empty.
7. Replaying the queue after recovery is automatic and in any order.
8. The inbox and failed-delivery stores follow the same privacy allowlist as the API: no contact or health fields.

## 3. Interim mitigation (no engineering, partial)
In n8n: switch on **Retry On Fail** for the HTTP Request node (a few tries with a wait), and add an **Error Workflow** that records failed executions and sends a notification.
Limits: (a) as far as I know, with Never Error on, HTTP error statuses do not count as node failures, so Retry On Fail would only trigger on network failures. **Verify in the n8n version in use (1.110.1).** (b) Retrying before SPEC-001 can create duplicates after a lost reply. (c) It does not give a durable queue.

## 4. Edge cases
| Case | Expected |
|------|----------|
| API down for 10 minutes | leads queue, then deliver automatically |
| API returns 500 for one specific lead repeatedly | retried up to the limit, then failed-delivery with the reason |
| API returns 422 | not retried; logged as rejected |
| API inserts the lead but the reply times out | retry returns 200 "already exists" (SPEC-001); one row |
| n8n itself is down | webhook caller must retry or the source must queue; document as a dependency |
| Queue store full or unavailable | alert immediately; webhook returns an error so the source can retry |
| Replay after recovery floods the API | rate-limited replay |

## 5. Acceptance criteria
- **Given** the API is stopped, **when** 50 leads arrive and the API is later restarted, **then** all 50 are stored exactly once.
- **Given** a lead the API will always reject with 422, **then** it is logged once as rejected and never retried.
- **Given** the API has been down longer than the alert threshold, **then** an alert is sent.
- **Given** the retry limit is reached, **then** the lead appears in failed-delivery with its reason and the number of attempts.

## 6. Test plan
Outage simulation (stop and restart the API during a load of test leads); timeout-after-insert simulation; classification tests for 4xx, 5xx, timeout, connection refused; replay-rate test; alert test.

## 7. Open questions
1. Acceptable maximum outage and acceptable delay before a lead is stored?
2. Where should the durable inbox live (queue service, database table, other)?
3. Who receives alerts, and through which channel?
4. Should the webhook acknowledge the source immediately and process later?
