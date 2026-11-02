"""API tests run against a COPY of funnel.db so the real data is never touched."""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from lead_api import create_app  # noqa: E402

SRC_DB = ROOT / "data" / "funnel.db"


@pytest.fixture()
def client(tmp_path):
    if not SRC_DB.exists():
        pytest.skip("Run src/generate_data.py first")
    db = tmp_path / "test.db"
    shutil.copy(SRC_DB, db)
    app = create_app(db)
    app.config["DB"] = db
    return app.test_client()


GOOD = {"utm_source": "Facebook", "utm_campaign": "pcos", "payer_type": "insurance"}


def lead_count(db):
    return sqlite3.connect(db).execute("SELECT COUNT(*) FROM fact_lead").fetchone()[0]


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_valid_lead_is_created_and_mapped(client):
    before = lead_count(client.application.config["DB"])
    r = client.post("/leads", json=GOOD)
    assert r.status_code == 201
    assert r.get_json()["channel"] == "Meta"
    assert lead_count(client.application.config["DB"]) == before + 1


def test_missing_fields_rejected(client):
    r = client.post("/leads", json={"utm_source": "google"})
    assert r.status_code == 400 and r.get_json()["error"] == "missing_fields"


def test_unknown_source_rejected_and_nothing_written(client):
    before = lead_count(client.application.config["DB"])
    r = client.post("/leads", json={**GOOD, "utm_source": "tiktok"})
    assert r.status_code == 422 and r.get_json()["error"] == "unknown_source"
    assert lead_count(client.application.config["DB"]) == before


def test_bad_payer_type_rejected(client):
    assert client.post("/leads", json={**GOOD, "payer_type": "cash"}).status_code == 422


def test_contact_or_health_fields_are_rejected(client):
    r = client.post("/leads", json={**GOOD, "email": "x@y.com", "diagnosis": "t2d"})
    assert r.status_code == 400 and r.get_json()["error"] == "unexpected_fields"


def test_non_json_body_rejected(client):
    assert client.post("/leads", data="hello").status_code == 400


def test_bad_timestamp_rejected(client):
    assert client.post("/leads", json={**GOOD, "created_at": "yesterday"}).status_code == 422
