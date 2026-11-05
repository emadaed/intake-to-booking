"""Weekly funnel report: a language model writes the sentences, CODE owns every number.

How it works
  1. Code computes the figures from funnel.db (this week vs the week before, plus a "matured cohort"
     of older leads whose appointments have had time to finish).
  2. The model gets a list of named placeholders such as {meta.spend} and must write 3-8 plain sentences
     using ONLY those placeholders. It never writes a digit.
  3. Code verifies the text (no digits, no unknown placeholders, no causes or advice, trend and ranking words
     only next to the matching placeholder) and then fills the placeholders in with the real values.
  4. If the text fails verification after 3 tries, or the model is unreachable, a fixed template is used.
     Unverified text is never published.

Honest limit: placeholders guarantee the NUMBERS, and the checks guard the WORDING. Nothing here proves the
prose is insightful or that every sentence is a fair reading of the numbers.

Run:   python src/weekly_report.py                    (uses Ollama, default model llama3.1:latest)
       python src/weekly_report.py --no-llm           (template only)
       python src/weekly_report.py --eval 10          (how often does the model pass verification on try 1?)
"""
import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "funnel.db"
REPORTS_DIR = ROOT / "reports"

PAID = ["Google", "Meta", "Microsoft"]
COHORT_OFFSET_DAYS = 28      # cohort = leads created 34..28 days before the report date
COHORT_LEN = 7
FLAT_PCT = 2.0               # changes smaller than this are called "flat"
DEFAULT_MODEL = "llama3.1:latest"
OLLAMA_URL = "http://localhost:11434/api/generate"
MAX_ATTEMPTS = 3


# ----------------------------------------------------------------------------- data
class NoSpendData(Exception):
    pass


def last_spend_date(con):
    v = con.execute("SELECT MAX(date) FROM fact_spend").fetchone()[0]
    if v is None:
        raise NoSpendData("fact_spend is empty. Run src/generate_data.py first.")
    return date.fromisoformat(v)


def flow_metrics(con, start, end):
    """Spend, leads, bookings per channel for start..end (inclusive), by each event's own date."""
    s, e = start.isoformat(), end.isoformat()
    out = {r[0]: {"spend": 0.0, "leads": 0, "bookings": 0} for r in con.execute("SELECT name FROM dim_channel")}
    for name, v in con.execute(
            "SELECT c.name, COALESCE(SUM(s.spend),0) FROM dim_channel c LEFT JOIN fact_spend s "
            "ON s.channel_id=c.channel_id AND s.date BETWEEN ? AND ? GROUP BY c.name", (s, e)):
        out[name]["spend"] = float(v)
    for name, v in con.execute(
            "SELECT c.name, COUNT(l.lead_id) FROM dim_channel c LEFT JOIN fact_lead l "
            "ON l.channel_id=c.channel_id AND date(l.created_at) BETWEEN ? AND ? GROUP BY c.name", (s, e)):
        out[name]["leads"] = v
    for name, v in con.execute(
            "SELECT c.name, COUNT(b.booking_id) FROM dim_channel c "
            "LEFT JOIN fact_lead l ON l.channel_id=c.channel_id "
            "LEFT JOIN fact_booking b ON b.lead_id=l.lead_id AND date(b.booked_at) BETWEEN ? AND ? "
            "GROUP BY c.name", (s, e)):
        out[name]["bookings"] = v
    return out


def cohort_metrics(con, start, end, as_of):
    """Leads created start..end and what is KNOWN about them as of `as_of`.
    Events after as_of are ignored, and an appointment scheduled after as_of counts as unfinished."""
    s, e, a = start.isoformat(), end.isoformat(), as_of.isoformat()
    out = {r[0]: {"spend": 0.0, "leads": 0, "bookings": 0, "occurred": 0, "no_show": 0, "cancelled": 0,
                  "unfinished": 0} for r in con.execute("SELECT name FROM dim_channel")}
    for name, v in con.execute(
            "SELECT c.name, COALESCE(SUM(s.spend),0) FROM dim_channel c LEFT JOIN fact_spend s "
            "ON s.channel_id=c.channel_id AND s.date BETWEEN ? AND ? GROUP BY c.name", (s, e)):
        out[name]["spend"] = float(v)
    for name, v in con.execute(
            "SELECT c.name, COUNT(l.lead_id) FROM dim_channel c LEFT JOIN fact_lead l "
            "ON l.channel_id=c.channel_id AND date(l.created_at) BETWEEN ? AND ? GROUP BY c.name", (s, e)):
        out[name]["leads"] = v
    rows = con.execute(
        "SELECT c.name, "
        " CASE WHEN date(b.scheduled_for) > ? THEN 'scheduled' ELSE a.status END AS st, COUNT(*) "
        "FROM fact_lead l JOIN dim_channel c ON c.channel_id=l.channel_id "
        "JOIN fact_booking b ON b.lead_id=l.lead_id AND date(b.booked_at) <= ? "
        "JOIN fact_appointment a ON a.booking_id=b.booking_id "
        "WHERE date(l.created_at) BETWEEN ? AND ? GROUP BY c.name, st", (a, a, s, e)).fetchall()
    for name, st, n in rows:
        out[name]["bookings"] += n
        key = {"occurred": "occurred", "no_show": "no_show", "cancelled": "cancelled"}.get(st, "unfinished")
        out[name][key] += n
    return out


def pct_change(cur, prev):
    return None if prev == 0 else (cur - prev) / prev * 100.0


def direction(cur, prev):
    """-> (word, text) for 'spend was {dir} {change}'."""
    ch = pct_change(cur, prev)
    if ch is None:
        return ("up", "from zero") if cur > 0 else ("flat", "at zero")
    if abs(ch) < FLAT_PCT:
        return "flat", f"within {FLAT_PCT:.0f}%"
    return ("up" if ch > 0 else "down"), f"{abs(ch):.0f}%"


def compute(con, as_of):
    wk_s, wk_e = as_of - timedelta(days=6), as_of
    pv_s, pv_e = as_of - timedelta(days=13), as_of - timedelta(days=7)
    co_e = as_of - timedelta(days=COHORT_OFFSET_DAYS)
    co_s = co_e - timedelta(days=COHORT_LEN - 1)
    cur, prev = flow_metrics(con, wk_s, wk_e), flow_metrics(con, pv_s, pv_e)
    coh = cohort_metrics(con, co_s, co_e, as_of)
    return {"as_of": as_of, "week": (wk_s, wk_e), "prev": (pv_s, pv_e), "cohort": (co_s, co_e),
            "cur": cur, "prev_m": prev, "coh": coh}


# ----------------------------------------------------------------------------- facts (placeholders)
def money0(x): return f"${x:,.0f}"
def money2(x): return f"${x:,.2f}"
def pct0(x): return f"{x * 100:.0f}%"
def num(x): return f"{int(x):,}"


def build_facts(data):
    """placeholder id -> (value text, description). IDs contain NO digits, so any digit the model writes is an error."""
    f = {}
    wk, pv, co = data["week"], data["prev"], data["cohort"]
    f["period.week_start"] = (wk[0].isoformat(), "first day of the report week")
    f["period.week_end"] = (wk[1].isoformat(), "last day of the report week")
    f["period.prev_start"] = (pv[0].isoformat(), "first day of the previous week")
    f["period.prev_end"] = (pv[1].isoformat(), "last day of the previous week")
    f["period.cohort_start"] = (co[0].isoformat(), "first creation day of the matured cohort")
    f["period.cohort_end"] = (co[1].isoformat(), "last creation day of the matured cohort")

    def add_flow(key, label, cur, prev):
        for metric, fmt in (("spend", money0), ("leads", num), ("bookings", num)):
            f[f"{key}.{metric}"] = (fmt(cur[metric]), f"{label} {metric} this week")
            d, ch = direction(cur[metric], prev[metric])
            f[f"{key}.{metric}_dir"] = (d, f"{label} {metric}: up, down or flat versus the previous week")
            f[f"{key}.{metric}_change"] = (ch, f"{label} {metric}: size of the change (use after the _dir word)")

    tot_cur = {m: sum(data["cur"][c][m] for c in PAID) for m in ("spend", "leads", "bookings")}
    tot_prev = {m: sum(data["prev_m"][c][m] for c in PAID) for m in ("spend", "leads", "bookings")}
    add_flow("total", "all paid channels", tot_cur, tot_prev)
    for ch in PAID:
        key = ch.lower()
        add_flow(key, ch, data["cur"][ch], data["prev_m"][ch])
        c = data["coh"][ch]
        final = c["occurred"] + c["no_show"] + c["cancelled"]
        f[f"{key}.cohort_leads"] = (num(c["leads"]), f"{ch} leads in the matured cohort")
        f[f"{key}.cohort_occurred"] = (num(c["occurred"]), f"{ch} occurred appointments in the matured cohort")
        f[f"{key}.cohort_show_rate"] = (pct0(c["occurred"] / final) if final else "n/a",
                                        f"{ch} share of finished appointments that occurred (matured cohort)")
        f[f"{key}.cohort_cost_per_occurred"] = (money2(c["spend"] / c["occurred"]) if c["occurred"] else "n/a",
                                                f"{ch} cost per occurred appointment (matured cohort)")
    # rankings, computed by code so the model never has to compare numbers itself
    cost = {ch: data["coh"][ch]["spend"] / data["coh"][ch]["occurred"] for ch in PAID if data["coh"][ch]["occurred"]}
    show = {}
    for ch in PAID:
        c = data["coh"][ch]
        final = c["occurred"] + c["no_show"] + c["cancelled"]
        if final:
            show[ch] = c["occurred"] / final
    spend_now = {ch: data["cur"][ch]["spend"] for ch in PAID}
    top = max(spend_now, key=spend_now.get) if any(spend_now.values()) else None
    f["rank.top_spend_channel"] = (top or "n/a", "paid channel with the highest spend this week")
    f["rank.top_spend"] = (money0(spend_now[top]) if top else "n/a", "that channel's spend this week")
    f["rank.cheapest_channel"] = (min(cost, key=cost.get) if cost else "n/a", "channel with the lowest cohort cost per occurred appointment")
    f["rank.cheapest_cost"] = (money2(min(cost.values())) if cost else "n/a", "that lowest cost")
    f["rank.priciest_channel"] = (max(cost, key=cost.get) if cost else "n/a", "channel with the highest cohort cost per occurred appointment")
    f["rank.priciest_cost"] = (money2(max(cost.values())) if cost else "n/a", "that highest cost")
    f["rank.highest_show_channel"] = (max(show, key=show.get) if show else "n/a", "channel with the highest cohort show rate")
    f["rank.highest_show_rate"] = (pct0(max(show.values())) if show else "n/a", "that highest show rate")
    f["rank.lowest_show_channel"] = (min(show, key=show.get) if show else "n/a", "channel with the lowest cohort show rate")
    f["rank.lowest_show_rate"] = (pct0(min(show.values())) if show else "n/a", "that lowest show rate")
    return f


# ----------------------------------------------------------------------------- verification
PLACEHOLDER = re.compile(r"\{([a-z_.]+)\}")
NUMBER_WORDS = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|twice|double|triple|half|dozen|hundred|thousand|million)\b", re.I)
CAUSAL = re.compile(r"\b(because|due to|caused|driven by|as a result|therefore|recommend\w*|should|must|suggest\w*|likely|probably)\b", re.I)
TREND = re.compile(r"\b(up|down|flat|stable|steady|unchanged|increase[ds]?|decrease[ds]?|rose|risen|rise|fell|fall|grew|grow\w*|dropped|drop|declined?|surged?|jumped?|improved?|worsened?)\b", re.I)
RANKING = re.compile(r"\b(higher|lower|highest|lowest|best|worst|cheaper|cheapest|pricier|priciest|better|worse|largest|smallest|biggest|leading|dominant|majority|contributors?|outperform\w*|underperform\w*)\b", re.I)
MARKDOWN = re.compile(r"[*#`|_~]|(^|\s)[-\u2022]\s")


def verify(text, facts):
    """-> (ok, reasons, filled_text). `filled_text` is only meaningful when ok."""
    reasons = []
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return False, ["empty output"], ""
    if len(t) > 1200:
        reasons.append("longer than 1200 characters")
    ids = PLACEHOLDER.findall(t)
    unknown = sorted({i for i in ids if i not in facts})
    if unknown:
        reasons.append("unknown placeholder(s): " + ", ".join(unknown))
    stripped = PLACEHOLDER.sub("", t)
    if "{" in stripped or "}" in stripped:
        reasons.append("malformed placeholder (stray brace)")
    if re.search(r"\d", stripped):
        reasons.append("digit written outside a placeholder")
    if NUMBER_WORDS.search(stripped):
        reasons.append("number word written outside a placeholder")
    if MARKDOWN.search(stripped):
        reasons.append("markdown or list formatting")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", t) if s]
    if not 3 <= len(sentences) <= 8:
        reasons.append(f"{len(sentences)} sentences (need 3 to 8)")
    if len(set(ids)) < 4:
        reasons.append("fewer than 4 different placeholders")
    if not any(("cohort" in i) or i.startswith("rank.") for i in ids):
        reasons.append("no matured-cohort or ranking placeholder")
    if CAUSAL.search(stripped):
        reasons.append("causal, advice or speculation wording")
    for s in sentences:
        sids = PLACEHOLDER.findall(s)
        if TREND.search(PLACEHOLDER.sub("", s)) and not any(i.endswith("_dir") or i.endswith("_change") for i in sids):
            reasons.append("trend word without a _dir/_change placeholder in the same sentence")
            break
    for s in sentences:
        sids = PLACEHOLDER.findall(s)
        if RANKING.search(PLACEHOLDER.sub("", s)) and not any(i.startswith("rank.") or i.endswith("_dir") for i in sids):
            reasons.append("comparison or ranking word without a rank.* or _dir placeholder in the same sentence")
            break
    if reasons:
        return False, reasons, ""
    return True, [], PLACEHOLDER.sub(lambda m: facts[m.group(1)][0], t)


# ----------------------------------------------------------------------------- the model
class ModelUnavailable(Exception):
    pass


def build_prompt(facts):
    lines = [
        "You write a short weekly funnel summary for a marketing and operations team.",
        "A program checks your text and rejects it if any rule is broken.",
        "RULES:",
        "1. Write 3 to 6 plain sentences in one paragraph. No lists, no headings, no markdown.",
        "2. NEVER write a digit or a number word. Every number, date, percentage and channel name from the data",
        "   must be written as a placeholder in curly braces, exactly as listed, for example {total.spend}.",
        "3. Use only the placeholders listed below, and at least four different ones.",
        "4. Do not explain causes, do not give advice, do not guess. Do not use: because, due to, should, recommend, likely.",
        "5. Words like up, down, flat, increase, decrease, higher, lower, highest, lowest, largest, best, worst, cheapest may only appear",
        "   in a sentence that also contains a placeholder ending in _dir, or one starting with rank.",
        "6. Mention at least one matured-cohort or rank. placeholder. The cohort is a group of older leads whose",
        "   appointments have had time to finish.",
        "",
        "PLACEHOLDERS (the current value is shown only so you understand the data; do not copy it):",
    ]
    for pid, (val, desc) in facts.items():
        lines.append(f"{{{pid}}} = {val}   ({desc})")
    lines += ["", "Write the summary now. Output only the paragraph."]
    return "\n".join(lines)


def ollama_generate(model, prompt, seed=42, temperature=0.0, timeout=600):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": temperature, "seed": seed}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["response"]
    except (urllib.error.URLError, TimeoutError, ConnectionError, KeyError, ValueError) as e:
        raise ModelUnavailable(f"{type(e).__name__}: {e}")


def fallback_template(facts):
    """Fixed template with placeholders. It uses the same placeholders as the model, so it is filled the same way."""
    parts = ["Paid spend was {total.spend} this week, {total.spend_dir} {total.spend_change} versus the previous week.",
             "Paid leads were {total.leads} ({total.leads_dir} {total.leads_change}) and bookings were "
             "{total.bookings} ({total.bookings_dir} {total.bookings_change})."]
    if facts["rank.cheapest_channel"][0] != "n/a":
        parts.append("In the matured cohort, {rank.cheapest_channel} had the lowest cost per occurred appointment at "
                     "{rank.cheapest_cost}, and {rank.priciest_channel} had the highest at {rank.priciest_cost}.")
    else:
        parts.append("No occurred appointments are available yet for the matured cohort, so cost per occurred "
                     "appointment is not reported.")
    if facts["rank.lowest_show_channel"][0] != "n/a":
        parts.append("The lowest show rate in the cohort was {rank.lowest_show_rate} ({rank.lowest_show_channel}) "
                     "and the highest was {rank.highest_show_rate} ({rank.highest_show_channel}).")
    return " ".join(parts)


def fallback_text(facts):
    return PLACEHOLDER.sub(lambda m: facts[m.group(1)][0], fallback_template(facts))


def write_summary(data, facts, generate, max_attempts=MAX_ATTEMPTS):
    """-> dict(source, text, attempts, log). `generate(prompt) -> str` is injected so tests need no model."""
    prompt, log, feedback = build_prompt(facts), [], ""
    for attempt in range(1, max_attempts + 1):
        try:
            raw = generate(prompt + feedback)
        except ModelUnavailable as e:
            log.append(f"attempt {attempt}: model unavailable ({e})")
            break
        ok, reasons, filled = verify(raw, facts)
        if ok:
            log.append(f"attempt {attempt}: passed verification")
            return {"source": "model", "text": filled, "attempts": attempt, "log": log}
        log.append(f"attempt {attempt}: rejected - " + "; ".join(reasons))
        feedback = "\n\nYour previous answer was rejected: " + "; ".join(reasons) + ". Write a new answer that follows every rule."
    return {"source": "template", "text": fallback_text(facts), "attempts": len(log), "log": log}


# ----------------------------------------------------------------------------- report text
def render(data, facts, summary):
    wk, pv, co = data["week"], data["prev"], data["cohort"]
    L = ["Weekly funnel report (synthetic demo data)",
         f"Week: {wk[0]} to {wk[1]}   (compared with {pv[0]} to {pv[1]})",
         f"Matured cohort for appointment outcomes: leads created {co[0]} to {co[1]}", ""]
    unfinished = sum(data["coh"][c]["unfinished"] for c in PAID)
    if unfinished:
        L += [f"Note: {unfinished} appointments in the cohort have no final outcome yet, so cohort figures may still change.", ""]
    L += ["Summary", summary["text"], "", "This week vs previous week (paid channels)",
          f"{'channel':<11}{'spend':>10}{'leads':>8}{'bookings':>10}   spend change"]
    for ch in PAID:
        k = ch.lower()
        L.append(f"{ch:<11}{facts[k + '.spend'][0]:>10}{facts[k + '.leads'][0]:>8}{facts[k + '.bookings'][0]:>10}"
                 f"   {facts[k + '.spend_dir'][0]} {facts[k + '.spend_change'][0]}")
    L += ["", "Matured cohort (appointment outcomes)",
          f"{'channel':<11}{'leads':>7}{'occurred':>10}{'show rate':>11}{'cost per occurred':>19}"]
    for ch in PAID:
        k = ch.lower()
        L.append(f"{ch:<11}{facts[k + '.cohort_leads'][0]:>7}{facts[k + '.cohort_occurred'][0]:>10}"
                 f"{facts[k + '.cohort_show_rate'][0]:>11}{facts[k + '.cohort_cost_per_occurred'][0]:>19}")
    L += ["", "Cohort sizes are small: do not read one week's differences between channels as a trend.", ""]
    if summary["source"] == "model":
        L.append(f"Method: summary written by a language model, then checked by code (attempt {summary['attempts']}). "
                 "Every number in the text was filled in from the database; the model wrote none.")
    elif not summary["log"]:
        L.append("Method: fixed template (language model not used). Every number comes from the database.")
    else:
        L.append("Method: fixed template. The language model was unavailable or its text failed verification "
                 "(see log below). Every number comes from the database.")
    L += ["Verification log: " + " | ".join(summary["log"]) if summary["log"] else "Verification log: template only (--no-llm)."]
    return "\n".join(L) + "\n"


def generate_report(db_path, as_of=None, model=DEFAULT_MODEL, use_llm=True, generate=None):
    """Build the whole report. `generate(prompt) -> str` can be injected (tests, API); default is Ollama.
    Raises NoSpendData when there is nothing to report on."""
    con = sqlite3.connect(db_path)
    try:
        as_of = as_of or last_spend_date(con)
        data = compute(con, as_of)
    finally:
        con.close()
    facts = build_facts(data)
    if use_llm:
        gen = generate or (lambda p: ollama_generate(model, p))
        summary = write_summary(data, facts, gen)
    else:
        summary = {"source": "template", "text": fallback_text(facts), "attempts": 0, "log": []}
    return {"as_of": as_of.isoformat(), "report": render(data, facts, summary), "source": summary["source"],
            "attempts": summary["attempts"], "log": summary["log"]}


def run_eval(facts, data, generate_with_seed, n):
    passed, reasons = 0, Counter()
    prompt = build_prompt(facts)
    for i in range(n):
        try:
            raw = generate_with_seed(prompt, i)
        except ModelUnavailable as e:
            reasons[f"model unavailable ({e})"] += 1
            continue
        ok, why, _ = verify(raw, facts)
        if ok:
            passed += 1
        for r in why:
            reasons[r] += 1
    return passed, reasons


# ----------------------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--as-of", help="report date YYYY-MM-DD (default: last date with ad spend)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-llm", action="store_true", help="use the fixed template only")
    ap.add_argument("--eval", type=int, metavar="N", help="measure how often the model passes verification on the first try")
    ap.add_argument("--out", help="write the report to this file (default: reports/weekly_<date>.txt)")
    a = ap.parse_args(argv)

    if not Path(a.db).exists():
        sys.exit("Database not found. Run src/generate_data.py first.")
    as_of = date.fromisoformat(a.as_of) if a.as_of else None

    if a.eval:
        con = sqlite3.connect(a.db)
        try:
            data = compute(con, as_of or last_spend_date(con))
        except NoSpendData as e:
            sys.exit(str(e))
        facts = build_facts(data)
        print(f"Asking {a.model} for {a.eval} independent summaries (temperature 0.7, no retries)...")
        gen = lambda prompt, i: ollama_generate(a.model, prompt, seed=i, temperature=0.7)
        passed, reasons = run_eval(facts, data, gen, a.eval)
        print(f"Passed verification on the first try: {passed} of {a.eval}")
        for r, n in reasons.most_common():
            print(f"  {n}x {r}")
        return

    if not a.no_llm:
        print(f"Asking {a.model} (CPU generation can take a minute or two)...")
    try:
        result = generate_report(a.db, as_of=as_of, model=a.model, use_llm=not a.no_llm)
    except NoSpendData as e:
        sys.exit(str(e))
    out = Path(a.out) if a.out else REPORTS_DIR / f"weekly_{result['as_of']}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result["report"], encoding="utf-8")
    print(result["report"])
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
