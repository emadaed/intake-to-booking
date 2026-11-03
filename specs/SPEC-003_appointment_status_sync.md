# SPEC-003 — Appointment status sync

**Status:** Proposed · **Priority:** P2 · **Backlog:** B-08 · **Owner:** Engineering · **Author:** Ahmad · **Date:** 2026-11-03

## 1. Context
`fact_appointment.status` (scheduled / occurred / no_show / cancelled) is simulated. The key business metric,
cost per **occurred** appointment, needs real outcomes from the scheduling or practice-management system
(system of record to be confirmed).

## 2. Requirements
1. Appointment outcomes flow from the system of record into `fact_appointment` automatically (webhook or scheduled polling, decided with the system owner).
2. Each appointment is linked to its booking through a **pseudonymous reference** (`booking_ref`) created at booking time. No patient name, contact details, diagnosis, or clinical notes are transferred.
3. Updates are idempotent: receiving the same update twice changes nothing.
4. Allowed status changes: `scheduled → occurred | no_show | cancelled`. Any other change (for example `occurred → scheduled`) is rejected and logged, except an explicit "correction" event from the source.
5. A reschedule updates `scheduled_for` and keeps the same booking and appointment.
6. An appointment becomes **final** once its status is not `scheduled` and a grace period (for example 24 hours) has passed; reports use final rows for show rate.
7. Unknown `booking_ref` values are stored in a quarantine table, not silently dropped.
8. Only the minimum necessary fields are requested from the source system.

## 3. Out of scope
Clinical data, billing status, reminder messages (B-10), anything that identifies a patient.

## 4. Data
`fact_appointment` gains `status_updated_at` and `source_event_id` (unique, for idempotency). New table `appointment_quarantine`.

## 5. Edge cases
| Case | Expected |
|------|----------|
| Same event delivered twice | one change applied |
| Events arrive out of order | latest by event time wins; older one logged |
| Cancelled after the appointment time | stored as received; flagged "late cancellation" |
| Rescheduled several times | one appointment row; history kept in a log |
| `booking_ref` not found | quarantined and alerted if the count rises |
| Source system down | polling resumes and catches up from the last successful time |
| Source sends extra personal fields | fields ignored and never stored; ingestion warns |

## 6. Acceptance criteria
- **Given** a booking exists, **when** the source reports "completed", **then** the appointment becomes `occurred` and appears in the occurred counts.
- **Given** the same event is sent twice, **then** nothing changes the second time.
- **Given** an event with an unknown `booking_ref`, **then** it is quarantined and not counted.
- **Given** a payload containing a patient name, **then** the name is not stored anywhere.

## 7. Test plan
Event-sequence tests (including out of order); idempotency test; quarantine test; a privacy test that fails if any non-allowlisted field is persisted; dashboard reconciliation against a sample export.

## 8. Open questions
1. Which system is the system of record, and does it offer webhooks or only polling?
2. Is a vendor agreement (such as a BAA) in place for the data path?
3. Can a pseudonymous `booking_ref` be created at booking time in that system?
4. What grace period defines a final status?
