"""Create the attribution views on data/funnel.db and print the channel summary."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "funnel.db"
VIEWS_PATH = ROOT / "sql" / "attribution_views.sql"


def main():
    con = sqlite3.connect(DB_PATH)
    con.executescript(VIEWS_PATH.read_text())
    con.commit()
    cols = ("channel", "spend", "leads", "bookings", "occurred", "show_rate",
            "cost_per_booking", "cost_per_occurred")
    rows = con.execute(f"SELECT {', '.join(cols)} FROM v_funnel_by_channel ORDER BY cost_per_occurred").fetchall()
    print(f"{'channel':10s}{'spend':>9s}{'leads':>7s}{'book':>6s}{'occ':>6s}{'show%':>7s}{'$/book':>9s}{'$/occ':>9s}")
    fmt = lambda v, f: "n/a" if v is None else format(v, f)
    for ch, sp, ld, bk, oc, sr, cpb, cpo in rows:
        print(f"{ch:10s}{sp:>9,.0f}{ld:>7d}{bk:>6d}{oc:>6d}{fmt(sr, '.0%'):>7s}{fmt(cpb, '.2f'):>9s}{fmt(cpo, '.2f'):>9s}")
    con.close()


if __name__ == "__main__":
    main()
