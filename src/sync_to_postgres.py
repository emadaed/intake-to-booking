"""Copy the funnel data from SQLite (operational) to Postgres (reporting) for Metabase.

SQLite stays the single source of truth, including the view logic: the two
reporting views are copied as plain tables (rpt_*), so SQL is not duplicated
in a second dialect. Each run is a FULL REFRESH, safe to repeat.

Prereqs:  docker compose up -d      (Postgres on localhost:5433)
          python src/build_views.py (creates the SQLite views)
Run:      python src/sync_to_postgres.py
"""
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "data" / "funnel.db"

PG = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5433")),
    dbname="reporting",
    user="itb",
)


def load_password():
    """POSTGRES_PASSWORD from the environment, else from the git-ignored .env file."""
    if os.getenv("POSTGRES_PASSWORD"):
        return os.environ["POSTGRES_PASSWORD"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("POSTGRES_PASSWORD="):
                return line.split("=", 1)[1].strip()
    sys.exit("POSTGRES_PASSWORD not found (set it in .env)")


# ---- converters: SQLite value -> Python value for Postgres ----
def _ts(v):    return None if v is None else datetime.fromisoformat(v).replace(tzinfo=None)
def _tstz(v):  return None if v is None else datetime.fromisoformat(v)
def _date(v):  return None if v is None else date.fromisoformat(v)
def _month(v): return None if v is None else date.fromisoformat(v + "-01")
def _bool(v):  return None if v is None else bool(v)
def _num(v):   return None if v is None else round(float(v), 4)
def _same(v):  return v


# (source in SQLite, target in Postgres, [(column, PG type, converter)], primary key)
TABLES = [
    ("dim_channel", "dim_channel", [
        ("channel_id", "integer", _same), ("name", "text", _same), ("is_paid", "boolean", _bool)], "channel_id"),
    ("fact_spend", "fact_spend", [
        ("spend_id", "integer", _same), ("date", "date", _date), ("channel_id", "integer", _same),
        ("campaign", "text", _same), ("spend", "numeric(12,2)", _num)], "spend_id"),
    ("fact_lead", "fact_lead", [
        ("lead_id", "integer", _same), ("created_at", "timestamp", _ts), ("channel_id", "integer", _same),
        ("campaign", "text", _same), ("payer_type", "text", _same)], "lead_id"),
    ("fact_booking", "fact_booking", [
        ("booking_id", "integer", _same), ("lead_id", "integer", _same),
        ("booked_at", "timestamp", _ts), ("scheduled_for", "timestamp", _ts)], "booking_id"),
    ("fact_appointment", "fact_appointment", [
        ("appointment_id", "integer", _same), ("booking_id", "integer", _same), ("status", "text", _same)],
     "appointment_id"),
    ("rejected_lead", "rejected_lead", [
        ("rejected_id", "integer", _same), ("received_at", "timestamptz", _tstz), ("rejected_at", "text", _same),
        ("http_status", "integer", _same), ("reason", "text", _same), ("detail", "text", _same),
        ("utm_source", "text", _same)], "rejected_id"),
    ("v_funnel_by_channel", "rpt_funnel_by_channel", [
        ("channel", "text", _same), ("is_paid", "boolean", _bool), ("spend", "numeric(12,2)", _num),
        ("leads", "integer", _same), ("bookings", "integer", _same), ("occurred", "integer", _same),
        ("no_show", "integer", _same), ("cancelled", "integer", _same), ("scheduled", "integer", _same),
        ("lead_to_booking_rate", "numeric(6,3)", _num), ("show_rate", "numeric(6,3)", _num),
        ("cost_per_lead", "numeric(12,2)", _num), ("cost_per_booking", "numeric(12,2)", _num),
        ("cost_per_occurred", "numeric(12,2)", _num)], None),
    ("v_funnel_monthly", "rpt_funnel_monthly", [
        ("month", "date", _month), ("channel", "text", _same), ("spend", "numeric(12,2)", _num),
        ("leads", "integer", _same), ("bookings", "integer", _same), ("occurred", "integer", _same),
        ("cost_per_occurred", "numeric(12,2)", _num)], None),
]
OPTIONAL = {"rejected_lead"}  # created by the API on first start; empty copy if absent


def sqlite_has(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def main():
    if not SQLITE_PATH.exists():
        sys.exit("data/funnel.db not found. Run src/generate_data.py first.")
    src = sqlite3.connect(SQLITE_PATH)

    for source, _, _, _ in TABLES:
        if not sqlite_has(src, source) and source not in OPTIONAL:
            sys.exit(f"'{source}' missing in SQLite. Run src/build_views.py (creates the views) and retry.")

    try:
        pg = psycopg.connect(**PG, password=load_password(), connect_timeout=5)
    except psycopg.OperationalError as e:
        sys.exit(f"Cannot connect to Postgres on {PG['host']}:{PG['port']}. Check that Docker is running "
                 f"('docker compose ps') and that POSTGRES_PASSWORD in .env matches the one used when the "
                 f"containers were first created.\n{str(e).splitlines()[0]}")

    results = []
    with pg:  # one transaction: all tables refresh together or not at all
        with pg.cursor() as cur:
            for source, target, cols, pk in TABLES:
                cur.execute(f'DROP TABLE IF EXISTS "{target}" CASCADE')
                ddl = ", ".join(f'"{n}" {t}' for n, t, _ in cols)
                if pk:
                    ddl += f', PRIMARY KEY ("{pk}")'
                cur.execute(f'CREATE TABLE "{target}" ({ddl})')

                n_src = 0
                if sqlite_has(src, source):
                    names = ", ".join(f'"{n}"' for n, _, _ in cols)
                    rows = [tuple(conv(v) for (_, _, conv), v in zip(cols, r))
                            for r in src.execute(f"SELECT {names} FROM {source}")]
                    n_src = len(rows)
                    if rows:
                        marks = ", ".join(["%s"] * len(cols))
                        cur.executemany(f'INSERT INTO "{target}" VALUES ({marks})', rows)
                cur.execute(f'SELECT COUNT(*) FROM "{target}"')
                n_pg = cur.fetchone()[0]
                if n_pg != n_src:
                    raise RuntimeError(f"{target}: SQLite has {n_src} rows, Postgres has {n_pg}")
                results.append((target, n_src, n_pg))

            # extra check: total spend must match
            s_sql = src.execute("SELECT ROUND(SUM(spend), 2) FROM fact_spend").fetchone()[0]
            cur.execute("SELECT SUM(spend) FROM fact_spend")
            s_pg = float(cur.fetchone()[0])
            if abs(s_sql - s_pg) > 0.01:
                raise RuntimeError(f"Total spend differs: SQLite {s_sql} vs Postgres {s_pg}")

    print(f"{'table':26s}{'sqlite':>8s}{'postgres':>10s}")
    for t, a, b in results:
        print(f"{t:26s}{a:>8,d}{b:>10,d}")
    print(f"\nTotal spend matches ({s_sql:,.2f}). Reporting database is up to date.")
    pg.close()
    src.close()


if __name__ == "__main__":
    main()
