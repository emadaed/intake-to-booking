"""Integrity tests for the synthetic funnel database. Run: pytest -v"""
import sqlite3
from pathlib import Path

import pytest

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "funnel.db"


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("Run src/generate_data.py and src/build_views.py first")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_every_booking_has_a_lead(con):
    assert one(con, """SELECT COUNT(*) FROM fact_booking b
                       LEFT JOIN fact_lead l USING(lead_id) WHERE l.lead_id IS NULL""") == 0


def test_every_appointment_has_a_booking(con):
    assert one(con, """SELECT COUNT(*) FROM fact_appointment a
                       LEFT JOIN fact_booking b USING(booking_id) WHERE b.booking_id IS NULL""") == 0


def test_funnel_is_monotonic(con):
    leads = one(con, "SELECT COUNT(*) FROM fact_lead")
    bookings = one(con, "SELECT COUNT(*) FROM fact_booking")
    occurred = one(con, "SELECT COUNT(*) FROM fact_appointment WHERE status='occurred'")
    assert occurred <= bookings <= leads


def test_booking_not_before_lead(con):
    assert one(con, """SELECT COUNT(*) FROM fact_booking b JOIN fact_lead l USING(lead_id)
                       WHERE b.booked_at < l.created_at""") == 0


def test_scheduled_not_before_booked(con):
    assert one(con, "SELECT COUNT(*) FROM fact_booking WHERE scheduled_for < booked_at") == 0


def test_view_spend_matches_fact_spend(con):
    """Guards against join fan-out inflating spend (tolerance covers per-row rounding)."""
    fact = one(con, "SELECT SUM(spend) FROM fact_spend")
    channel_view = one(con, "SELECT SUM(spend) FROM v_funnel_by_channel")
    monthly_view = one(con, "SELECT SUM(spend) FROM v_funnel_monthly")
    assert fact == pytest.approx(channel_view, abs=1.0)
    assert fact == pytest.approx(monthly_view, abs=1.0)


def test_view_counts_match_fact_tables(con):
    total_leads = one(con, "SELECT COUNT(*) FROM fact_lead")
    total_occ = one(con, "SELECT COUNT(*) FROM fact_appointment WHERE status='occurred'")
    assert one(con, "SELECT SUM(leads) FROM v_funnel_by_channel") == total_leads
    assert one(con, "SELECT SUM(leads) FROM v_funnel_monthly") == total_leads
    assert one(con, "SELECT SUM(occurred) FROM v_funnel_monthly") == total_occ


def test_unpaid_channels_have_no_cost_metrics(con):
    rows = con.execute("SELECT cost_per_occurred FROM v_funnel_by_channel WHERE is_paid = 0").fetchall()
    assert rows and all(r[0] is None for r in rows)
