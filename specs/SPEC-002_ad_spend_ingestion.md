# SPEC-002 — Daily ad-spend ingestion

**Status:** Proposed · **Priority:** P2 · **Backlog:** B-09 · **Owner:** Engineering · **Author:** Ahmad · **Date:** 2026-11-03

## 1. Context
`fact_spend` is filled by a synthetic generator. Cost-per-booking and cost-per-occurred-appointment are only
trustworthy if spend comes from the ad platforms themselves (Google, Meta, Microsoft) and matches their reports.

## 2. Requirements
1. A scheduled job runs once a day and loads spend per **date, channel, campaign** into `fact_spend`.
2. Each run re-pulls the **last N days** (default 7, configurable) because platforms revise recent spend; existing rows are **updated**, not duplicated (upsert on date + channel + campaign).
3. Amounts are stored in one reporting currency (USD); the original currency and amount are kept in extra columns.
4. Dates use one agreed reporting timezone, documented in the job configuration.
5. One platform failing must not block the others; the job records per-platform success or failure.
6. Credentials live in a secret store or environment variables, never in the repository.
7. Campaign names must follow a convention that matches `utm_campaign` on leads (see open questions); unmatched campaigns are loaded and flagged, not dropped.
8. A run log records start, end, rows inserted, rows updated, and errors.

## 3. Out of scope
Creative-level or keyword-level spend; budget changes; writing anything back to ad platforms.

## 4. Data
`fact_spend` changes: unique key on (`date`, `channel_id`, `campaign`); new columns `currency`, `amount_original`, `loaded_at`. New table `ingestion_run` for the run log.

## 5. Edge cases
| Case | Expected |
|------|----------|
| Platform revises yesterday's spend | row updated, `loaded_at` changes |
| Campaign renamed in the platform | new name appears as a new campaign; flagged for review |
| Campaign deleted | historical rows kept |
| Day with zero spend | row stored with 0 (not missing) |
| Platform API unavailable or rate limited | other platforms still load; run marked partial; alert sent; next run backfills |
| Currency other than USD | converted using a documented rate source; original kept |
| Daylight-saving or account-timezone differences | all dates converted to the reporting timezone |
| Job runs twice by mistake | no duplicate rows |

## 6. Acceptance criteria
- **Given** a platform report totaling X for a date range, **when** the job has run, **then** `fact_spend` totals X for that range within the agreed tolerance.
- **Given** the job is run twice for the same day, **then** row counts are identical after the second run.
- **Given** Meta's API returns an error, **then** Google and Microsoft rows still load and the run log shows Meta failed.
- **Given** a campaign name with no matching lead campaign, **then** it is loaded and appears in an "unmatched campaigns" report.

## 7. Test plan
Recorded sample responses (fixtures) per platform; upsert and revision tests; partial-failure test; timezone boundary test; reconciliation check against a manually exported platform report.

## 8. Open questions
1. Which platforms are actually in use, and who owns the credentials?
2. Reporting timezone and currency?
3. Who maintains the campaign naming convention, and how are existing campaigns mapped?
4. How far back should the first load go?
