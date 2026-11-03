# Automation Backlog — Intake-to-Booking Attribution & AI Ops Pipeline

> **Scope note.** This is a portfolio project on **synthetic data**. The backlog is modeled on a public job
> description (AI Operations & Automation Manager) and on gaps found while building this repo. It does **not**
> describe any real company's systems. "Built" means built in this repo; "Proposed" means not built.

Last updated: 2026-11-03 · Owner of the backlog: Ahmad

## How the build-vs-engineer call is made

An item goes to **engineering** if any of these is true; otherwise it is built with AI / no-code tools:

1. It writes to or reads from a production system of record (EHR, billing, scheduling) that needs credentials and an audit trail.
2. It touches patient-identifiable information (PHI), so access control and a vendor agreement matter.
3. It must keep working when a person is not watching (retries, queues, alerting, idempotency).
4. It needs schema changes or data migrations that other reports depend on.

## Backlog

| ID | Item | Status | Route | Why this route | Depends on | Success measure | Priority |
|----|------|--------|-------|----------------|------------|-----------------|----------|
| B-01 | Lead intake and attribution pipeline (webhook → clean → validate → store) | **Built** | No-code (n8n) + small API | Orchestration is no-code; the strict validation/write lives in a small API | — | Valid leads stored; invalid ones rejected with a reason; 21 automated tests pass | Done |
| B-02 | Rejected-lead monitoring (dead-letter table + dashboard chart) | **Built** | No-code + small API | Same pattern as B-01 | B-01 | Every rejected lead is logged with a reason and shown in Metabase | Done |
| B-03 | Spend-to-appointment dashboard (cost per booking vs per occurred appointment) | **Built** | BI tool (Metabase) | Read-only reporting on a copy of the data | B-01 | 4 dashboard charts; totals reconcile with source (checked on every sync) | Done |
| B-04 | Staff knowledge assistant (policy Q&A, refuses PHI, escalates to a human) | Proposed (project step 9) | AI workflow | Read-only over approved documents; no system-of-record writes | Approved policy documents | Answers cite a source; 100% of PHI test prompts refused in the test set | P2 |
| B-05 | Matomo event and dimension design | Proposed (project step 10) | Configuration + documentation | Design work; no custom code | B-01 | Every funnel step has a named event; spend-to-booking join keys documented | P2 |
| B-06 | Duplicate lead protection | Proposed | **Engineering** | Schema change + idempotency rules (criteria 3, 4) | — | Zero duplicate leads when the same submission is sent twice | **P1** → SPEC-001 |
| B-07 | Resilient intake when the API is down | Proposed | **Engineering** | Queue, retries, alerting (criterion 3); n8n Retry On Fail alone did not rescue a lead in testing | SPEC-001 | No lead lost during a simulated API outage; failures alert within a set time | **P1** → SPEC-004 |
| B-08 | Appointment status sync from the scheduling/EHR system | Proposed | **Engineering** | Production system access, PHI risk (criteria 1, 2) | SPEC-001 | Occurred / no-show status updated within a set time of the visit | P2 → SPEC-003 |
| B-09 | Daily ad-spend ingestion (Google, Meta, Microsoft) | Proposed | **Engineering** | Credentials, scheduling, late revisions to spend (criteria 1, 3) | Campaign naming convention | Spend in the database matches platform reports within an agreed tolerance | P2 → SPEC-002 |
| B-10 | No-show reminder follow-up flow | Proposed | No-code (n8n or CRM workflow) | Message sending is a standard workflow once B-08 supplies status | B-08 | Fewer no-shows (baseline and target to be agreed) | P3 |

## Priority reasoning

- **P1 first:** B-06 and B-07 are weaknesses found in the existing build. A lost or duplicated lead corrupts every number downstream.
- **P2 next:** B-08 and B-09 replace simulated data (appointment outcomes, ad spend) with real feeds. B-04 and B-05 are buildable now.
- **P3 last:** B-10 only pays off once B-08 exists.

## Known limits of the current build (stated plainly)

- All data is simulated. The channel results (for example Meta's show rate) are assumptions chosen for the demo, not findings.
- The system stores no contact details, so it cannot recognize the same person submitting twice. SPEC-001 deals only with the same *submission* sent twice.
- October looks low on the dashboard because appointments after 31 October are still "scheduled", not yet "occurred".
