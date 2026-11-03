-- The four Metabase dashboard questions (run against the Postgres reporting database).

-- 1. Cost per booking vs per occurred appointment (paid channels)
SELECT channel,
       cost_per_booking  AS "Cost per booking",
       cost_per_occurred AS "Cost per occurred appointment"
FROM rpt_funnel_by_channel
WHERE is_paid
ORDER BY channel;

-- 2. Show rate by channel (occurred / finished appointments)
SELECT channel,
       ROUND(show_rate * 100, 1) AS "Show rate (%)"
FROM rpt_funnel_by_channel
ORDER BY show_rate DESC;

-- 3. Occurred appointments per month, by channel
SELECT month,
       channel,
       occurred AS "Occurred appointments"
FROM rpt_funnel_monthly
WHERE month < DATE '2026-11-01'
ORDER BY month, channel;

-- 4. Rejected leads by reason (data quality monitor)
SELECT reason,
       COUNT(*) AS "Rejected leads"
FROM rejected_lead
GROUP BY reason
ORDER BY "Rejected leads" DESC;
