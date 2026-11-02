"""Generate SYNTHETIC funnel data for the Intake-to-Booking project.

No real patient or company data is used. Every number here is simulated.
Run:  python src/generate_data.py
"""
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
START = date(2026, 5, 1)
END = date(2026, 10, 31)          # last day of simulated data
AS_OF = END                        # appointments after this date are still 'scheduled'

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "funnel.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"

# name: (is_paid, avg_daily_spend, cost_per_lead, p_booking, p_occurred, p_no_show)
# Remaining probability after occurred + no_show = cancelled.
# Chosen so channels rank differently on cost-per-booking vs cost-per-occurred.
CHANNELS = {
    "Google":    (1, 140.0, 38.0, 0.46, 0.80, 0.08),
    "Meta":      (1, 110.0, 17.0, 0.40, 0.52, 0.34),
    "Microsoft": (1,  45.0, 30.0, 0.42, 0.74, 0.12),
    "Organic":   (0,   0.0,  0.0, 0.50, 0.82, 0.07),
    "Referral":  (0,   0.0,  0.0, 0.62, 0.88, 0.05),
}
ORGANIC_LEADS_PER_DAY = {"Organic": 3.0, "Referral": 1.2}
CAMPAIGNS = {
    "Google": ["brand", "diabetes", "weight_mgmt"],
    "Meta": ["awareness", "pcos", "retargeting"],
    "Microsoft": ["brand", "nutrition_counseling"],
    "Organic": ["seo"],
    "Referral": ["provider_referral"],
}


def poisson(rng, lam):
    """Small Poisson sampler (Knuth); stdlib only."""
    if lam <= 0:
        return 0
    import math
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def main():
    rng = random.Random(SEED)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA_PATH.read_text())

    channel_ids = {}
    for name, cfg in CHANNELS.items():
        cur = con.execute("INSERT INTO dim_channel(name, is_paid) VALUES (?,?)", (name, cfg[0]))
        channel_ids[name] = cur.lastrowid

    leads, bookings, appts = [], [], []
    spend_rows = []
    lead_id = booking_id = appt_id = 0

    day = START
    while day <= END:
        weekend = day.weekday() >= 5
        for name, (paid, avg_spend, cpl, p_book, p_occ, p_ns) in CHANNELS.items():
            cid = channel_ids[name]
            campaigns = CAMPAIGNS[name]
            if paid:
                # spend per campaign, lower on weekends, with noise
                daily = avg_spend * (0.7 if weekend else 1.05) * rng.uniform(0.8, 1.2)
                per_campaign = daily / len(campaigns)
                for camp in campaigns:
                    s = round(per_campaign * rng.uniform(0.7, 1.3), 2)
                    spend_rows.append((day.isoformat(), cid, camp, s))
                    n_leads = poisson(rng, s / cpl)
                    for _ in range(n_leads):
                        leads.append((cid, camp, day, rng))
            else:
                n_leads = poisson(rng, ORGANIC_LEADS_PER_DAY[name] * (0.6 if weekend else 1.1))
                for _ in range(n_leads):
                    leads.append((cid, campaigns[0], day, rng))
        day += timedelta(days=1)

    cid_to_name = {v: k for k, v in channel_ids.items()}
    for cid, camp, d, _ in leads:
        lead_id += 1
        created = datetime(d.year, d.month, d.day, rng.randint(7, 21), rng.randint(0, 59))
        payer = "insurance" if rng.random() < 0.82 else "self_pay"
        con.execute(
            "INSERT INTO fact_lead VALUES (?,?,?,?,?)",
            (lead_id, created.isoformat(timespec="minutes"), cid, camp, payer),
        )
        _, _, _, p_book, p_occ, p_ns = CHANNELS[cid_to_name[cid]]
        if rng.random() >= p_book:
            continue
        booking_id += 1
        booked = created + timedelta(hours=rng.expovariate(1 / 30))  # ~30h average delay
        scheduled = booked + timedelta(days=rng.randint(1, 10))
        con.execute(
            "INSERT INTO fact_booking VALUES (?,?,?,?)",
            (booking_id, lead_id, booked.isoformat(timespec="minutes"),
             scheduled.isoformat(timespec="minutes")),
        )
        appt_id += 1
        if scheduled.date() > AS_OF:
            status = "scheduled"
        else:
            r = rng.random()
            status = "occurred" if r < p_occ else ("no_show" if r < p_occ + p_ns else "cancelled")
        con.execute("INSERT INTO fact_appointment VALUES (?,?,?)", (appt_id, booking_id, status))

    con.executemany(
        "INSERT INTO fact_spend(date, channel_id, campaign, spend) VALUES (?,?,?,?)", spend_rows
    )
    con.commit()
    summarize(con)
    con.close()


def summarize(con):
    print(f"\nDatabase written to: {DB_PATH}\n")
    for t in ("dim_channel", "fact_spend", "fact_lead", "fact_booking", "fact_appointment"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t:18s} {n:>7,d} rows")

    print("\nCost per booking vs cost per OCCURRED appointment (paid channels):")
    rows = con.execute("""
        WITH s AS (SELECT channel_id, SUM(spend) AS spend FROM fact_spend GROUP BY channel_id),
        f AS (
          SELECT l.channel_id,
                 COUNT(DISTINCT l.lead_id) AS leads,
                 COUNT(DISTINCT b.booking_id) AS bookings,
                 SUM(CASE WHEN a.status='occurred' THEN 1 ELSE 0 END) AS occurred
          FROM fact_lead l
          LEFT JOIN fact_booking b ON b.lead_id = l.lead_id
          LEFT JOIN fact_appointment a ON a.booking_id = b.booking_id
          GROUP BY l.channel_id)
        SELECT c.name, s.spend, f.leads, f.bookings, f.occurred,
               s.spend / f.bookings, s.spend / f.occurred
        FROM dim_channel c JOIN s USING(channel_id) JOIN f USING(channel_id)
        ORDER BY 6
    """).fetchall()
    print(f"{'channel':10s}{'spend':>10s}{'leads':>7s}{'book':>6s}{'occ':>6s}{'$/book':>9s}{'$/occ':>9s}")
    for n, sp, ld, bk, oc, cpb, cpo in rows:
        print(f"{n:10s}{sp:>10,.0f}{ld:>7d}{bk:>6d}{oc:>6d}{cpb:>9.2f}{cpo:>9.2f}")


if __name__ == "__main__":
    main()
