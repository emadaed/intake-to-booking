-- Intake-to-Booking schema (synthetic data only)
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS fact_appointment;
DROP TABLE IF EXISTS fact_booking;
DROP TABLE IF EXISTS fact_lead;
DROP TABLE IF EXISTS fact_spend;
DROP TABLE IF EXISTS dim_channel;

CREATE TABLE dim_channel (
    channel_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    is_paid      INTEGER NOT NULL CHECK (is_paid IN (0,1))
);

CREATE TABLE fact_spend (
    spend_id     INTEGER PRIMARY KEY,
    date         TEXT NOT NULL,
    channel_id   INTEGER NOT NULL REFERENCES dim_channel(channel_id),
    campaign     TEXT NOT NULL,
    spend        REAL NOT NULL CHECK (spend >= 0)
);

CREATE TABLE fact_lead (
    lead_id      INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    channel_id   INTEGER NOT NULL REFERENCES dim_channel(channel_id),
    campaign     TEXT NOT NULL,
    payer_type   TEXT NOT NULL CHECK (payer_type IN ('insurance','self_pay'))
);

CREATE TABLE fact_booking (
    booking_id    INTEGER PRIMARY KEY,
    lead_id       INTEGER NOT NULL UNIQUE REFERENCES fact_lead(lead_id),
    booked_at     TEXT NOT NULL,
    scheduled_for TEXT NOT NULL
);

CREATE TABLE fact_appointment (
    appointment_id INTEGER PRIMARY KEY,
    booking_id     INTEGER NOT NULL UNIQUE REFERENCES fact_booking(booking_id),
    status         TEXT NOT NULL CHECK (status IN ('scheduled','occurred','no_show','cancelled'))
);

CREATE INDEX idx_spend_date_channel ON fact_spend(date, channel_id);
CREATE INDEX idx_lead_channel ON fact_lead(channel_id, created_at);
