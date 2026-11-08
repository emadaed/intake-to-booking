# Case study — Intake-to-Booking Attribution & AI Ops Pipeline

**Author:** Ahmad · **Date:** November 2026 · **Data:** synthetic · **Repo:** github.com/emadaed/intake-to-booking

## The problem
A clinic that buys ads usually sees *leads* and *bookings*. The number that pays the bills is **appointments that
actually happen**. If one channel books cheaply but people don't show up, the cheapest-looking channel is not the cheapest.
The goal was to build the full path from spend to occurred appointment, with automation in front and a dashboard at the end.

## How the work was done
The problem is real; the data is not. I built the project in phases, running and testing each piece on my own machine.
I used an AI assistant (Claude) to design and draft code and documents; I ran every command, checked every output, and
made the decisions about scope, design and what to record. The mistakes below are real and are kept in on purpose.

## What I built
- **Synthetic funnel** (5 channels, about 6 months): spend, leads, bookings, appointments, with a fixed random seed so results are repeatable.
- **SQL attribution views** and **8 data tests** (no orphan rows, funnel never grows, spend totals reconcile).
- **Lead intake API** (Flask) that validates strictly and writes to the database, plus **13 API tests**.
- **Staff knowledge assistant** over fictional policy documents: quotes approved text only, with 50 tests for its guardrails.
- **Weekly funnel report:** a local language model (Ollama) writes the sentences; code fills in and verifies every number, with a fixed-template fallback, an API endpoint and a weekly n8n schedule. **124 automated tests** in total.
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
0. **Quote, don't write.** The knowledge assistant returns a passage copied word for word from a current approved document, with its ID and effective date. It cannot invent a policy. Its rules (emergency wording, patient identifiers, clinical questions, no-evidence abstention) are plain code in a fixed order, not prompt instructions, because code can be tested and a prompt can be talked around.
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
| My own test question "How much notice is needed to cancel?" got no answer | The policy said "24 hours before" and never used the word "notice"; the search matches words | Rewrote the document in the words staff actually use and added a few filler words to the stop list. The general limit (paraphrases can be missed) remains and is documented |
| The weekly report's checker approved a sentence calling two channels "the largest contributors" | "Largest" was not on my list of ranking words | Added the words, gave the model a code-computed "top spend" fact, and re-tested on the live output |
| The report named one channel as having the highest show rate when two tied at 67% | Ties were decided on unrounded values | Ties are decided on the displayed value and reported together |
| The model wrote its own direction word ("decreasing by within 2%") beside my number, when the channel was flat | I only checked that a direction placeholder was somewhere in the sentence | The model may no longer write direction words; a change placeholder must directly follow its own direction placeholder; a channel name must match the data in its sentence |
| Only 3 of 10 summaries passed after I tightened the checker | A prompt rule alone did not stop the model writing digits and direction words | One worked example in the prompt raised first-try passes to 7 of 10 (small sample) |
| **A lead was lost when the API was stopped** | The HTTP node's "Never Error" setting only handles error *responses*, not a missing response | Written up as SPEC-004; see below |

### The gap I found in my own design
With "Never Error" on, a 422 from the API is routed to a rejected branch. But with the API stopped there is no response at all: the node
failed with `ECONNREFUSED` and the lead existed only in n8n's execution history. I also noticed that the rejected-lead log calls the same API, so it fails too, and that a
422 (do not retry) and a 500 (retry) take the same path. I then tested n8n's "Retry On Fail" with the API stopped: no retries were observed and the
lead was still lost. A real fix needs a durable inbox and classified retries, and retries are only safe once duplicates are
prevented (SPEC-001). SPEC-004 describes this.

## The weekly report: the model writes words, code owns the numbers
The model gets named placeholders such as `{total.spend}` and writes the sentences around them. Code computes every figure from the database, fills the placeholders in, and rejects text that breaks the rules. After three rejected tries the report falls back to a fixed template, and an unverified summary is never published. A "matured cohort" rule keeps recent, unfinished appointments out of the cost-per-occurred figures.

Measured on a local 8B model: 7 of 10 summaries passed on the first try with the final checker and prompt (3 of 10 before the worked example), and a separate run of 5 complete reports passed on the first attempt 5 times. These are small samples on one model. The checks guarantee the numbers and guard the wording; they cannot prove a sentence is a fair reading of the numbers, which is why reading the live output mattered: it found three gaps my tests had not.

## What I would do next
1. Build SPEC-001 (deduplication), then SPEC-004 (outage handling).
2. Run Matomo locally, send simulated events following `docs/matomo_event_spec.md`, and record where reality differs from the design.
3. Deliver the weekly report (email or chat) and re-measure its pass rate on more runs and a second model.
4. Replace simulated spend and appointment outcomes with real feeds (SPEC-002, SPEC-003).

## Honest limits
- Simulated data; assumed channel behavior.
- The assistant was tested only on questions written by its author. Guardrail behavior is verified; answer accuracy on real staff questions is not. It shows the closest approved passage and cannot judge whether that passage answers the question. The privacy check errs toward refusing and cannot reliably detect patient names.
- No GoHighLevel, Matomo or HIPAA compliance experience is claimed. HIPAA appears here only as a design principle (minimum necessary data).
- The weekly report's pass rates come from small samples on one local model; its schedule runs only while my machine, n8n and the API are on, and it does not send the report anywhere yet.
- The Matomo design has not been run against a Matomo server.
- Local, single-machine setup; no authentication, monitoring or production hardening.

## Skills this project shows
| Skill | Where it shows up |
|---|---|
| Hands-on SQL and data modeling | Schema, views, reconciliation tests |
| No-code automation, webhooks, REST APIs | n8n workflow calling the API |
| Spec writing and build-vs-engineer judgment | Backlog and four specs |
| Dashboards | Metabase, four charts |
| Judgment with sensitive data | Field allowlist; no contact or health data stored |
| AI guardrails and testing | Knowledge assistant: deterministic refusals and abstention, 50 tests |
| LLM workflow with verified output | Weekly report: placeholders, code checks, template fallback, measured pass rates |
| Light Python | API, generator, sync script, tests |
