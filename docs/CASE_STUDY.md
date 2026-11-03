# Case study — Intake-to-Booking Attribution & AI Ops Pipeline

**Author:** Ahmad · **Date:** November 2026 · **Data:** synthetic · **Repo:** github.com/emadaed/intake-to-booking

## The problem
A clinic that buys ads usually sees *leads* and *bookings*. The number that pays the bills is **appointments that
actually happen**. If one channel books cheaply but people don't show up, the cheapest-looking channel is not the cheapest.
The goal was to build the full path from spend to occurred appointment, with automation in front and a dashboard at the end.

## How the work was done
I followed a public job description for an AI operations and automation role, turned its requirements into a build plan, and
built it in phases, running and testing each piece on my own machine. I used an AI assistant (Claude) to design and draft code and documents;
I ran every command, checked every output, and made the decisions about scope, design and what to record. The mistakes below are real and are kept in on purpose.

## What I built
- **Synthetic funnel** (5 channels, about 6 months): spend, leads, bookings, appointments, with a fixed random seed so results are repeatable.
- **SQL attribution views** and **8 data tests** (no orphan rows, funnel never grows, spend totals reconcile).
- **Lead intake API** (Flask) that validates strictly and writes to the database, plus **13 API tests**.
- **n8n workflow:** webhook → clean → call API → route success (insurance or self-pay follow-up) or failure (logged to a rejected-lead table).
- **Postgres reporting copy** and a **Metabase dashboard** with four charts.
- **A backlog and four engineering specs** for the things I did not build.

## Result (simulated)
| Channel | Show rate | Cost per booking | Cost per occurred appointment |
|---|---|---|---|
| Meta | 49% | $41.22 | $87.13 |
| Google | 82% | $81.99 | $106.26 |
| Microsoft | 77% | $80.66 | $106.82 |

These rates were chosen when generating the data, to see whether the pipeline would expose the difference. It did, but this
proves the pipeline works, not that Meta behaves this way in real life.

## Decisions that mattered
1. **Aggregate spend separately before joining.** Joining spend straight onto leads repeats each spend row for every lead and inflates cost. A test now guards this.
2. **A strict field allowlist in the API.** The system is attribution-only, so contact and health fields are refused instead of silently dropped. This is minimum-necessary thinking, not HIPAA experience.
3. **n8n orchestrates, a small API owns the write.** n8n has no SQLite node, and the strict validation belongs in code that can be tested.
4. **A Postgres reporting copy.** I could not verify official SQLite support in Metabase, so I did not build on it. The operational database stays SQLite; a sync script refreshes Postgres and checks row counts and total spend.

## What went wrong, and what it taught me
| What happened | Cause | Fix and lesson |
|---|---|---|
| 3 tests failed after saving files | The SQL views had never been created; I had not made "build the views" an explicit step | Dependencies between steps must be written down, not implied |
| n8n said the webhook "is not registered" | Test mode listens for exactly one call | Click Listen before every test; the permanent URL has no such limit |
| The API rejected a lead with `unexpected_fields: status` | My n8n node sent an extra field | The strict allowlist worked as designed; I removed the field |
| Metabase refused the database login | Most likely browser autofill replacing the typed login (not confirmed) | Set a known password and used a private window |
| The Y-axis had no values in the point-and-click builder | Metabase did not offer the numeric columns | Rebuilt the chart from a SQL question |
| **A lead was lost when the API was stopped** | The HTTP node's "Never Error" setting only handles error *responses*, not a missing response | Written up as SPEC-004; see below |

### The gap I found in my own design
With "Never Error" on, a 422 from the API is routed to a rejected branch. But with the API stopped there is no response at all: the node
failed with `ECONNREFUSED` and the lead existed only in n8n's execution history. I also noticed that the rejected-lead log calls the same API, so it fails too, and that a
422 (do not retry) and a 500 (retry) take the same path. I then tested n8n's "Retry On Fail" with the API stopped: no retries were observed and the
lead was still lost. A real fix needs a durable inbox and classified retries, and retries are only safe once duplicates are
prevented (SPEC-001). SPEC-004 describes this.

## What I would do next
1. Build SPEC-001 (deduplication), then SPEC-004 (outage handling).
2. Add the staff knowledge assistant with tests that it refuses requests for patient information.
3. Document Matomo events and dimensions for each funnel step.
4. Replace simulated spend and appointment outcomes with real feeds (SPEC-002, SPEC-003).

## Honest limits
- Simulated data; assumed channel behavior.
- No GoHighLevel, Matomo or HIPAA compliance experience is claimed. HIPAA appears here only as a design principle (minimum necessary data).
- Local, single-machine setup; no authentication, monitoring or production hardening.

## Skills shown, by evidence
| Requirement from the job description | Where it shows up |
|---|---|
| Hands-on SQL and data modeling | Schema, views, reconciliation tests |
| No-code automation, webhooks, REST APIs | n8n workflow calling the API |
| Spec writing and build-vs-engineer judgment | Backlog and four specs |
| Dashboards | Metabase, four charts |
| Judgment with sensitive data | Field allowlist; no contact or health data stored |
| Light Python | API, generator, sync script, tests |
