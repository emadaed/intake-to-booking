"""Tests for the weekly report. A stand-in 'model' is injected, so no Ollama is needed.

Honest scope: these prove the verification and the arithmetic. They do not prove that a real model's prose is
insightful; run `python src/weekly_report.py --eval 10` on your machine to measure how often the real model passes.
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import weekly_report as wr  # noqa: E402

AS_OF = date(2026, 10, 31)
G, M, MS = 1, 2, 3


@pytest.fixture()
def con(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.executescript((ROOT / "sql" / "schema.sql").read_text())
    for cid, name, paid in [(1, "Google", 1), (2, "Meta", 1), (3, "Microsoft", 1), (4, "Organic", 0), (5, "Referral", 0)]:
        c.execute("INSERT INTO dim_channel VALUES (?,?,?)", (cid, name, paid))
    ids = {"spend": 0, "lead": 0, "booking": 0}

    def spend(ch, start_day, end_day, amount, month=10):
        for d in range(start_day, end_day + 1):
            ids["spend"] += 1
            c.execute("INSERT INTO fact_spend VALUES (?,?,?,?,?)", (ids["spend"], f"2026-{month:02d}-{d:02d}", ch, "c", amount))

    def lead(ch, created, booked=None, sched=None, status="scheduled"):
        ids["lead"] += 1
        c.execute("INSERT INTO fact_lead VALUES (?,?,?,?,?)", (ids["lead"], created + "T10:00", ch, "c", "insurance"))
        if booked:
            ids["booking"] += 1
            c.execute("INSERT INTO fact_booking VALUES (?,?,?,?)", (ids["booking"], ids["lead"], booked + "T09:00", sched + "T09:00"))
            c.execute("INSERT INTO fact_appointment VALUES (?,?,?)", (ids["booking"], ids["booking"], status))

    spend(G, 18, 31, 100); spend(M, 25, 31, 50); spend(M, 18, 24, 25)           # report week and previous week
    spend(G, 27, 30, 10, month=9); spend(G, 1, 3, 10); spend(M, 27, 30, 20, month=9); spend(M, 1, 3, 20)  # cohort window
    # current week (Oct 25-31)
    lead(G, "2026-10-26", "2026-10-27", "2026-11-05"); lead(G, "2026-10-27", "2026-10-29", "2026-11-06")
    lead(G, "2026-10-28"); lead(G, "2026-10-30", "2026-11-01", "2026-11-08")      # booking AFTER as_of: must be ignored
    lead(M, "2026-10-26"); lead(M, "2026-10-30", "2026-10-31", "2026-11-07")
    lead(G, "2026-11-02", "2026-11-03", "2026-11-09")                              # lead AFTER as_of: must be ignored
    # previous week (Oct 18-24)
    for d, b in [("19", "20"), ("20", "21"), ("21", "22")]:
        lead(G, f"2026-10-{d}", f"2026-10-{b}", "2026-10-30")
    lead(M, "2026-10-20", "2026-10-21", "2026-10-30")
    # matured cohort (leads created Sep 27 - Oct 3)
    lead(G, "2026-09-28", "2026-09-29", "2026-10-02", "occurred")
    lead(G, "2026-09-29", "2026-09-30", "2026-10-04", "occurred")
    lead(G, "2026-09-30", "2026-10-01", "2026-10-08", "occurred")
    lead(G, "2026-10-01", "2026-10-02", "2026-10-06", "no_show")
    lead(M, "2026-09-28", "2026-09-29", "2026-10-03", "occurred")
    lead(M, "2026-09-29", "2026-09-30", "2026-10-03", "cancelled")
    lead(M, "2026-09-30", "2026-10-01", "2026-10-04", "no_show")
    lead(M, "2026-10-01", "2026-10-02", "2026-10-05", "no_show")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def facts(con):
    return wr.build_facts(wr.compute(con, AS_OF))


GOOD = ("Total paid spend was {total.spend} this week, {total.spend_dir} {total.spend_change} on the previous week. "
        "Paid leads were {total.leads} and bookings were {total.bookings}. "
        "In the matured cohort, {rank.cheapest_channel} had the lowest cost per occurred appointment at {rank.cheapest_cost}.")


# ---- the arithmetic --------------------------------------------------------------------------
def test_week_windows_and_as_of_cutoff(con):
    cur = wr.flow_metrics(con, date(2026, 10, 25), date(2026, 10, 31))
    prev = wr.flow_metrics(con, date(2026, 10, 18), date(2026, 10, 24))
    assert cur["Google"] == {"spend": 700.0, "leads": 4, "bookings": 2}     # Nov 1 booking and Nov 2 lead excluded
    assert cur["Meta"] == {"spend": 350.0, "leads": 2, "bookings": 1}
    assert prev["Google"] == {"spend": 700.0, "leads": 3, "bookings": 3}
    assert prev["Meta"] == {"spend": 175.0, "leads": 1, "bookings": 1}


def test_cohort_metrics_final_outcomes(con):
    coh = wr.cohort_metrics(con, date(2026, 9, 27), date(2026, 10, 3), AS_OF)
    assert (coh["Google"]["occurred"], coh["Google"]["no_show"], coh["Google"]["spend"]) == (3, 1, 70.0)
    assert (coh["Meta"]["occurred"], coh["Meta"]["cancelled"], coh["Meta"]["no_show"]) == (1, 1, 2)
    assert coh["Google"]["unfinished"] == 0


def test_cohort_ignores_the_future_as_of_an_earlier_date(con):
    """With as_of Oct 6, an appointment scheduled Oct 8 is unfinished even though the table says 'occurred'."""
    c = wr.cohort_metrics(con, date(2026, 9, 27), date(2026, 10, 3), date(2026, 10, 6))["Google"]
    assert (c["bookings"], c["occurred"], c["no_show"], c["unfinished"]) == (4, 2, 1, 1)
    early = wr.cohort_metrics(con, date(2026, 9, 27), date(2026, 10, 3), date(2026, 10, 1))["Google"]
    assert (early["bookings"], early["occurred"], early["unfinished"]) == (3, 0, 3)   # booking made Oct 2 not known yet


def test_default_report_date_is_the_last_spend_date(con):
    assert wr.last_spend_date(con) == AS_OF


def test_direction_words():
    assert wr.direction(200, 100) == ("up", "100%")
    assert wr.direction(50, 100) == ("down", "50%")
    assert wr.direction(101, 100) == ("flat", "within 2%")
    assert wr.direction(5, 0) == ("up", "from zero")
    assert wr.direction(0, 0) == ("flat", "at zero")


def test_facts_values_and_rankings(facts):
    assert facts["total.spend"][0] == "$1,050" and facts["total.spend_change"][0] == "20%"
    assert facts["total.leads"][0] == "6" and facts["total.bookings"][0] == "3"
    assert facts["google.cohort_cost_per_occurred"][0] == "$23.33"
    assert facts["meta.cohort_show_rate"][0] == "25%"
    assert (facts["rank.cheapest_channel"][0], facts["rank.priciest_channel"][0]) == ("Google", "Meta")
    assert (facts["rank.highest_show_rate"][0], facts["rank.lowest_show_rate"][0]) == ("75%", "25%")
    assert facts["microsoft.spend_dir"][0] == "flat"


def test_placeholder_ids_never_contain_digits(facts):
    assert not [k for k in facts if any(ch.isdigit() for ch in k)]


# ---- verification ---------------------------------------------------------------------------------
def test_good_text_passes_and_numbers_come_from_the_database(facts):
    ok, why, filled = wr.verify(GOOD, facts)
    assert ok, why
    assert filled.startswith("Total paid spend was $1,050 this week, up 20% on the previous week.")
    assert "Google had the lowest cost per occurred appointment at $23.33" in filled


BAD = [
    ("digit", GOOD.replace("{total.leads}", "6"), "digit"),
    ("number word", GOOD.replace("Paid leads were", "Paid leads were about twice what"), "number word"),
    ("unknown placeholder", GOOD.replace("{total.leads}", "{total.visitors}"), "unknown placeholder"),
    ("stray brace", GOOD + " Extra {oops.", "stray brace"),
    ("causal", GOOD.replace("Paid leads were", "Paid leads were higher because"), "causal"),
    ("markdown", "**" + GOOD, "markdown"),
    ("bullet list", "- " + GOOD, "markdown"),
    ("too short", "Spend was {total.spend}. Leads were {total.leads}.", "sentences"),
    ("too few placeholders", "Spend was {total.spend}. Leads were {total.leads}. Bookings were {total.bookings}.", "fewer than 4"),
    ("no cohort", ("Spend was {total.spend}, {total.spend_dir} {total.spend_change}. Leads were {total.leads}. "
                   "Bookings were {total.bookings}."), "matured-cohort"),
    ("trend without dir", GOOD.replace("{total.spend_dir} {total.spend_change}", "{total.spend_change}").replace("Paid leads were", "Leads rose and were"), "trend word"),
    ("ranking without rank", GOOD.replace("{rank.cheapest_channel} had the lowest cost per occurred appointment at {rank.cheapest_cost}", "{google.cohort_show_rate} of Google visits was the best outcome"), "ranking word"),
    ("largest without rank", GOOD + " Google was the largest contributor to {google.spend}.", "ranking word"),
    ("up without dir", GOOD + " Google leads were up on {google.cohort_leads}.", "trend word"),
    ("empty", "   ", "empty"),
]


@pytest.mark.parametrize("label,text,expected", BAD, ids=[b[0] for b in BAD])
def test_bad_text_is_rejected_for_the_right_reason(facts, label, text, expected):
    ok, why, filled = wr.verify(text, facts)
    assert not ok and filled == ""
    assert any(expected in r for r in why), why


def test_verify_never_returns_text_with_digits_in_it_unless_filled(facts):
    _, _, filled = wr.verify(GOOD, facts)
    assert "{" not in filled and "}" not in filled


# ---- the retry loop and the fallback --------------------------------------------------------------------
def test_second_attempt_can_pass(con, facts):
    data, replies = wr.compute(con, AS_OF), iter(["Spend was 100 dollars.", GOOD])
    s = wr.write_summary(data, facts, lambda p: next(replies))
    assert s["source"] == "model" and s["attempts"] == 2 and "rejected" in s["log"][0]


def test_rejection_reasons_are_fed_back_to_the_model(con, facts):
    data, prompts = wr.compute(con, AS_OF), []

    def gen(p):
        prompts.append(p)
        return GOOD if len(prompts) == 2 else "Spend was 100."
    wr.write_summary(data, facts, gen)
    assert "Your previous answer was rejected" in prompts[1] and "Your previous answer was rejected" not in prompts[0]


def test_three_bad_answers_fall_back_to_the_template(con, facts):
    s = wr.write_summary(wr.compute(con, AS_OF), facts, lambda p: "I think spend rose because 5 ads worked.")
    assert s["source"] == "template" and len(s["log"]) == 3
    assert "$1,050" in s["text"] and "because" not in s["text"]


def test_model_unavailable_falls_back_without_crashing(con, facts):
    def gen(p):
        raise wr.ModelUnavailable("connection refused")
    s = wr.write_summary(wr.compute(con, AS_OF), facts, gen)
    assert s["source"] == "template" and "unavailable" in s["log"][0]


def test_the_fallback_template_passes_its_own_verification(facts):
    ok, why, filled = wr.verify(wr.fallback_template(facts), facts)
    assert ok, why
    assert filled == wr.fallback_text(facts)


def test_prompt_lists_every_placeholder_and_the_rules(facts):
    p = wr.build_prompt(facts)
    assert all("{" + k + "}" in p for k in facts)
    assert "NEVER write a digit" in p


def test_empty_database_still_produces_a_report(tmp_path):
    c = sqlite3.connect(tmp_path / "e.db")
    c.executescript((ROOT / "sql" / "schema.sql").read_text())
    for cid, name, paid in [(1, "Google", 1), (2, "Meta", 1), (3, "Microsoft", 1), (4, "Organic", 0), (5, "Referral", 0)]:
        c.execute("INSERT INTO dim_channel VALUES (?,?,?)", (cid, name, paid))
    data = wr.compute(c, AS_OF)
    f = wr.build_facts(data)
    s = {"source": "template", "text": wr.fallback_text(f), "attempts": 0, "log": []}
    out = wr.render(data, f, s)
    assert "No occurred appointments are available" in out and "n/a" in out


# ---- measurement and command line ------------------------------------------------------------------------
def test_eval_counts_first_try_passes_and_reasons(con, facts):
    replies = iter([GOOD, "Spend was 100.", GOOD, "bad"])
    passed, reasons = wr.run_eval(facts, wr.compute(con, AS_OF), lambda p, i: next(replies), 4)
    assert passed == 2 and sum(reasons.values()) >= 2


def test_command_line_template_report_is_written(con, tmp_path):
    db = con.execute("PRAGMA database_list").fetchone()[2]
    out = tmp_path / "r.txt"
    wr.main(["--db", db, "--as-of", "2026-10-31", "--no-llm", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "Weekly funnel report (synthetic demo data)" in text and "$1,050" in text
    assert "language model not used" in text


def test_top_spend_fact_exists_so_the_model_can_name_the_biggest_channel(facts):
    assert facts["rank.top_spend_channel"][0] == "Google" and facts["rank.top_spend"][0] == "$700"
    ok, why, filled = wr.verify(GOOD + " The largest spend was {rank.top_spend_channel} at {rank.top_spend}.", facts)
    assert ok, why
    assert "The largest spend was Google at $700." in filled


def test_report_always_carries_the_small_sample_note(con):
    data = wr.compute(con, AS_OF)
    f = wr.build_facts(data)
    out = wr.render(data, f, {"source": "template", "text": wr.fallback_text(f), "attempts": 0, "log": []})
    assert "do not read one week's differences between channels as a trend" in out


def test_generate_report_with_injected_model(con):
    db = con.execute("PRAGMA database_list").fetchone()[2]
    r = wr.generate_report(db, as_of=AS_OF, generate=lambda p: GOOD)
    assert r["source"] == "model" and r["attempts"] == 1 and "$1,050" in r["report"]


def test_generate_report_raises_when_there_is_no_spend(tmp_path):
    c = sqlite3.connect(tmp_path / "n.db")
    c.executescript((ROOT / "sql" / "schema.sql").read_text())
    c.commit(); c.close()
    with pytest.raises(wr.NoSpendData):
        wr.generate_report(tmp_path / "n.db")
