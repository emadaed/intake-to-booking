"""Minimal lead-intake API: the 'engineering' piece that n8n calls.

n8n orchestrates (receive, normalize, route). This service owns the one thing
that must be strict: validating a lead and writing it to the database.

Privacy by design: this project is attribution-only, so NO contact details
or health information are accepted or stored. Unknown fields are rejected.

Run:  python src/lead_api.py     (listens on 127.0.0.1:5000)
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "funnel.db"

# utm_source (lowercased) -> dim_channel.name
SOURCE_TO_CHANNEL = {
    "google": "Google", "adwords": "Google",
    "meta": "Meta", "facebook": "Meta", "instagram": "Meta",
    "bing": "Microsoft", "microsoft": "Microsoft",
    "organic": "Organic", "seo": "Organic",
    "referral": "Referral", "provider_referral": "Referral",
}
ALLOWED_FIELDS = {"utm_source", "utm_campaign", "payer_type", "created_at"}
PAYER_TYPES = {"insurance", "self_pay"}


def create_app(db_path=DEFAULT_DB):
    app = Flask(__name__)

    def connect():
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/leads")
    def create_lead():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="invalid_json", detail="Body must be a JSON object"), 400

        extra = set(body) - ALLOWED_FIELDS
        if extra:
            # Reject rather than silently drop: surfaces upstream mapping mistakes
            # and stops PHI/contact data from sneaking into this system.
            return jsonify(error="unexpected_fields", detail=sorted(extra)), 400

        missing = [f for f in ("utm_source", "utm_campaign", "payer_type") if not body.get(f)]
        if missing:
            return jsonify(error="missing_fields", detail=missing), 400

        channel = SOURCE_TO_CHANNEL.get(str(body["utm_source"]).strip().lower())
        if channel is None:
            return jsonify(error="unknown_source", detail=body["utm_source"]), 422

        payer = str(body["payer_type"]).strip().lower()
        if payer not in PAYER_TYPES:
            return jsonify(error="invalid_payer_type", detail=body["payer_type"]), 422

        created = body.get("created_at")
        if created:
            try:
                created = datetime.fromisoformat(created).isoformat(timespec="minutes")
            except (TypeError, ValueError):
                return jsonify(error="invalid_created_at", detail="Use ISO 8601"), 422
        else:
            created = datetime.now().isoformat(timespec="minutes")

        campaign = str(body["utm_campaign"]).strip()[:80]
        with connect() as con:
            row = con.execute("SELECT channel_id FROM dim_channel WHERE name = ?", (channel,)).fetchone()
            if row is None:
                return jsonify(error="channel_not_configured", detail=channel), 500
            cur = con.execute(
                "INSERT INTO fact_lead(created_at, channel_id, campaign, payer_type) VALUES (?,?,?,?)",
                (created, row[0], campaign, payer),
            )
        return jsonify(lead_id=cur.lastrowid, channel=channel, payer_type=payer), 201

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)
