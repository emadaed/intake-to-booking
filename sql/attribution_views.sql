-- Attribution views: spend -> lead -> booking -> occurred appointment
-- Spend and funnel counts are aggregated SEPARATELY, then joined,
-- so one-to-many joins can never inflate spend (fan-out trap).

DROP VIEW IF EXISTS v_funnel_by_channel;
DROP VIEW IF EXISTS v_funnel_monthly;

CREATE VIEW v_funnel_by_channel AS
WITH spend AS (
    SELECT channel_id, SUM(spend) AS spend
    FROM fact_spend GROUP BY channel_id
),
funnel AS (
    SELECT l.channel_id,
           COUNT(*)                                          AS leads,
           COUNT(b.booking_id)                               AS bookings,
           SUM(CASE WHEN a.status = 'occurred'  THEN 1 ELSE 0 END) AS occurred,
           SUM(CASE WHEN a.status = 'no_show'   THEN 1 ELSE 0 END) AS no_show,
           SUM(CASE WHEN a.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN a.status = 'scheduled' THEN 1 ELSE 0 END) AS scheduled
    FROM fact_lead l
    LEFT JOIN fact_booking b     ON b.lead_id = l.lead_id
    LEFT JOIN fact_appointment a ON a.booking_id = b.booking_id
    GROUP BY l.channel_id
)
SELECT c.name AS channel,
       c.is_paid,
       ROUND(COALESCE(s.spend, 0), 2)                              AS spend,
       f.leads, f.bookings, f.occurred, f.no_show, f.cancelled, f.scheduled,
       ROUND(1.0 * f.bookings / f.leads, 3)                        AS lead_to_booking_rate,
       -- rate uses only appointments with a final status (excludes still-scheduled)
       ROUND(1.0 * f.occurred / NULLIF(f.occurred + f.no_show + f.cancelled, 0), 3) AS show_rate,
       ROUND(s.spend / NULLIF(f.leads, 0), 2)                      AS cost_per_lead,
       ROUND(s.spend / NULLIF(f.bookings, 0), 2)                   AS cost_per_booking,
       ROUND(s.spend / NULLIF(f.occurred, 0), 2)                   AS cost_per_occurred
FROM dim_channel c
JOIN funnel f ON f.channel_id = c.channel_id
LEFT JOIN spend s ON s.channel_id = c.channel_id;

CREATE VIEW v_funnel_monthly AS
WITH spine AS (
    SELECT strftime('%Y-%m', date) AS month, channel_id FROM fact_spend
    UNION
    SELECT strftime('%Y-%m', created_at), channel_id FROM fact_lead
),
spend AS (
    SELECT strftime('%Y-%m', date) AS month, channel_id, SUM(spend) AS spend
    FROM fact_spend GROUP BY 1, 2
),
funnel AS (
    SELECT strftime('%Y-%m', l.created_at) AS month, l.channel_id,
           COUNT(*) AS leads,
           COUNT(b.booking_id) AS bookings,
           SUM(CASE WHEN a.status = 'occurred' THEN 1 ELSE 0 END) AS occurred
    FROM fact_lead l
    LEFT JOIN fact_booking b     ON b.lead_id = l.lead_id
    LEFT JOIN fact_appointment a ON a.booking_id = b.booking_id
    GROUP BY 1, 2
)
SELECT sp.month, c.name AS channel,
       ROUND(COALESCE(s.spend, 0), 2) AS spend,
       COALESCE(f.leads, 0)    AS leads,
       COALESCE(f.bookings, 0) AS bookings,
       COALESCE(f.occurred, 0) AS occurred,
       ROUND(s.spend / NULLIF(f.occurred, 0), 2) AS cost_per_occurred
FROM spine sp
JOIN dim_channel c ON c.channel_id = sp.channel_id
LEFT JOIN spend  s ON s.month = sp.month AND s.channel_id = sp.channel_id
LEFT JOIN funnel f ON f.month = sp.month AND f.channel_id = sp.channel_id;
