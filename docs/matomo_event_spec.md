# Matomo event and dimension design — Intake-to-Booking

**Status:** Design only. Matomo has **not** been installed for this project and nothing below has been run against a Matomo server.
**Backlog:** B-05 · **Author:** Ahmad · **Date:** 2026-11-03

## 1. Purpose and split of responsibilities

The funnel is **ad click → visit → lead → booking → occurred appointment**. Two systems each do what they are good at:

| System | Owns | Does not own |
|---|---|---|
| **Matomo** | How people arrive and what they do on the public site before they become a lead: source, campaign, landing page, form start and submit | Bookings, appointments, spend |
| **Database** (`funnel.db`, Postgres copy) | Leads, bookings, appointment outcomes, ad spend: the system of record and the source of all dashboard numbers | Browser behavior |

Later stages (booking, appointment outcome) are sent to Matomo as server-side events only so one journey can be viewed in one place.
**If Matomo and the database disagree, the database wins**, and the difference is monitored (section 7).

## 2. Events

Rules: lowercase `snake_case`; category = funnel stage; action = what happened; name = a non-identifying descriptor or empty.
Event value (`e_v`) is left empty: no revenue data exists, and Matomo requires it to be numeric.

| # | Category / action | Name | Fired by | Trigger |
|---|---|---|---|---|
| 1 | *(page view, not an event)* | — | Browser | Landing page loads with campaign parameters in the URL |
| 2 | `booking_form` / `started` | — | Browser | First interaction with the intake form |
| 3 | `lead` / `created` | — | Server | The lead API answered 201 |
| 4 | `lead` / `rejected` | the **reason code** only (for example `unknown_source`) | Server | The lead API refused the lead |
| 5 | `booking` / `confirmed` | — | Server | A booking is recorded |
| 6 | `appointment` / `occurred` | — | Server | Final status is occurred |
| 7 | `appointment` / `no_show` | — | Server | Final status is no-show |
| 8 | `appointment` / `cancelled` | — | Server | Final status is cancelled |

Server-side events use Matomo's Tracking HTTP API (`e_c`, `e_a`, `e_n`). The API reference says `ca=1` may accompany any
request that is not a page view; whether it is needed here is to be confirmed on a real install.

## 3. Custom dimensions

Matomo assigns each dimension's numeric ID when it is created, so IDs are **not** fixed in this document.
Visit-scope values stay attached to the whole visit; action-scope values belong to one event. A server-side request sends them as `dimensionN=value`.

| Name | Scope | Allowed values | Notes |
|---|---|---|---|
| `channel` | Visit | Google, Meta, Microsoft, Organic, Referral | Same names as `dim_channel`. Derived from `utm_source` with the **same mapping the lead API uses**, kept in one shared list so the two cannot drift |
| `campaign` | Visit | the `utm_campaign` value | Matomo's built-in campaign report already records the first campaign of a visit; this dimension exists to attach it to server-sent events |
| `payer_type` | Action | `insurance`, `self_pay` | Sent with `lead` / `created` only |
| `lead_ref` | Action | opaque random ID | The join key to the database (see section 5). Never derived from personal data |

Check the number of custom dimensions allowed on the chosen Matomo setup before creating these; limits exist and differ by hosting.

## 4. Campaign URL standard

Every paid and tracked link uses this pattern (`example.com` is a placeholder):

`https://example.com/book?utm_source=meta&utm_medium=cpc&utm_campaign=pcos_awareness`

- `utm_source` and `utm_campaign` are required; `utm_medium` is `cpc`, `organic` or `referral`.
- Values: lowercase, letters, digits and underscores only; no spaces; no personal data.
- The campaign name in the ad platform **must equal** `utm_campaign`, because ad spend joins to leads on it (SPEC-002, open question 3).
- `utm_source` maps to a channel with the same list as the lead API: google/adwords → Google; meta/facebook/instagram → Meta; bing/microsoft → Microsoft; organic/seo → Organic; referral/provider_referral → Referral.
- According to Matomo's documentation, Matomo reads `utm_*` parameters by default, and its free Marketing Campaigns Reporting plugin adds separate source, medium and content reports. Confirm on the real install which one is needed.

## 5. How the events join to spend and appointments

```
ad spend (date, channel, campaign)  ──►  lead (created date, channel, campaign)   joined at campaign-day level
lead ──► booking              via lead_id
booking ──► appointment       via booking_id
Matomo event ──► lead         via lead_ref  (= external_ref from SPEC-001)
```

- Ad platforms report spend per campaign per day, never per person, so attribution is at **campaign level**, not person level.
- Rule used here: a lead is credited to the **first campaign seen in the visit that created it**. Multi-touch attribution is out of scope.
- `lead_ref` requires SPEC-001 (deduplication) to exist, because that is where an external reference is introduced.

## 6. Privacy rules (a design checklist, not legal advice)

1. No names, emails, phone numbers, dates of birth, member IDs, diagnoses or free text in any event category, action, name or value, any dimension, or any tracked URL.
2. `lead_ref` is random and opaque. Do not use a hash of an email or phone number: such hashes can be reversed by guessing.
3. Track only an allowlist of query parameters (the `utm_*` set); drop everything else from tracked URLs.
4. Do not track inside the patient area or visit room, only the public marketing and booking pages.
5. Do not set Matomo's User ID to anything identifying.
6. Even without identifiers, a page or campaign about a specific health condition can reveal something about a visitor. How specific campaign and page names may be is a decision for the privacy officer.
7. Open decisions for the privacy officer: IP anonymization, cookie and consent approach, data retention, and hosting (self-hosted or vendor), including whether a vendor agreement is needed.

## 7. Data quality checks

| Check | Rule |
|---|---|
| Missing channel or campaign | 0% of leads |
| Unknown `utm_source` | Count of `lead` / `rejected` events with reason `unknown_source`; investigate any rise |
| Matomo versus database | Daily `lead` / `created` events equal leads in the database within a tolerance to be agreed |
| Order per `lead_ref` | lead, then booking, then appointment; never reversed |
| Duplicate events | 0 duplicate `lead_ref` on `lead` / `created` (needs SPEC-001) |
| Visits without a campaign | Reported as "direct"; share monitored for sudden changes |

## 8. Acceptance criteria

- **Given** a visitor lands on a link with `utm_source=meta` and `utm_campaign=pcos_awareness`, **then** the visit shows channel Meta and that campaign.
- **Given** the lead API answers 201, **then** exactly one `lead` / `created` event exists with a `lead_ref` and a `payer_type`.
- **Given** the lead API refuses a lead, **then** one `lead` / `rejected` event exists whose name is the reason code and contains no submitted text.
- **Given** a request containing an email address in any event field, **then** a test fails (the sender must block it).
- **Given** a tracked URL with an unknown query parameter, **then** the parameter is not stored.
- **Given** one day of data, **then** Matomo lead counts match the database within the agreed tolerance.

## 9. Not decided or out of scope

Multi-touch attribution; revenue tracking; ad-platform click IDs; the consent banner design; a tag manager versus direct tracking code.

## 10. What would make this real

Run Matomo locally in Docker, create the four dimensions, send simulated events with a short script using the parameters above, and then **update this document with what actually happened**, including every place where reality differed from the design.

## References checked on 2026-11-03

- Matomo Tracking HTTP API reference: event parameters, custom dimensions with visit and action scope, `ca`, campaign overrides
- Matomo FAQ on tracking campaigns: default campaign parameters, including `utm_*`
- Matomo Marketing Campaigns Reporting plugin repository: extra campaign dimensions
