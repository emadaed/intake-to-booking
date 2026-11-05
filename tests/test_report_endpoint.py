"""The report endpoint, tested on a COPY of data/funnel.db with a stand-in model (no Ollama needed)."""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from lead_api import create_app  # noqa: E402

SRC_DB = ROOT / "data" / "funnel.db"
GOOD = ("Total paid spend was {total.spend} this week, {total.spend_dir} {total.spend_change} versus the previous "
        "week. Paid leads were {total.leads} and bookings were {total.bookings}. In the matured cohort, "
        "{rank.lowest_show_channel} had the lowest show rate at {rank.lowest_show_rate}.")


@pytest.fixture()
def db(tmp_path):
    if not SRC_DB.exists():
        pytest.skip("Run src/generate_data.py first")
    p = tmp_path / "t.db"
    shutil.copy(SRC_DB, p)
    return p


def client_for(db, generate=None):
    return create_app(db, report_generate=generate).test_client()


def test_template_report_without_a_model(db):
    r = client_for(db).get("/reports/weekly?no_llm=true")
    j = r.get_json()
    assert r.status_code == 200 and j["source"] == "template"
    assert "Weekly funnel report (synthetic demo data)" in j["report"]


def test_model_text_is_verified_and_filled_in(db):
    j = client_for(db, lambda p: GOOD).get("/reports/weekly").get_json()
    assert j["source"] == "model" and j["attempts"] == 1
    assert "{" not in j["report"] and "$" in j["report"]


def test_bad_model_text_falls_back_to_the_template(db):
    j = client_for(db, lambda p: "Spend rose to 5 because of ads.").get("/reports/weekly").get_json()
    assert j["source"] == "template" and len(j["log"]) == 3


def test_unreachable_model_falls_back_to_the_template(db):
    import weekly_report

    def down(p):
        raise weekly_report.ModelUnavailable("connection refused")
    j = client_for(db, down).get("/reports/weekly").get_json()
    assert j["source"] == "template" and "unavailable" in j["log"][0]


def test_as_of_parameter_is_used(db):
    j = client_for(db).get("/reports/weekly?no_llm=true&as_of=2026-10-15").get_json()
    assert j["as_of"] == "2026-10-15" and "Week: 2026-10-09 to 2026-10-15" in j["report"]


def test_bad_parameters_are_rejected(db):
    c = client_for(db)
    assert c.get("/reports/weekly?as_of=yesterday").status_code == 400
    assert c.get("/reports/weekly?model=bad%20name;rm").status_code == 400


def test_no_spend_data_gives_409(tmp_path):
    p = tmp_path / "empty.db"
    c = sqlite3.connect(p)
    c.executescript((ROOT / "sql" / "schema.sql").read_text())
    c.commit(); c.close()
    r = client_for(p).get("/reports/weekly?no_llm=true")
    assert r.status_code == 409 and r.get_json()["error"] == "no_spend_data"
