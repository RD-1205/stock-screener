"""Write docs/DATA-STATUS.md: how fresh the data is after the nightly refresh.

    python scripts/data_status.py --db screener.db --log <refresh log> --out <file>

The nightly job (scripts/daily_refresh.ps1) commits this file to main, so
the repo itself shows whether the pipeline is alive and how current the
data behind the site is, in line with the site's "we show our work" rule.
Opens the database read-only.
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import sentiment  # noqa: E402

RUN_HEADER = re.compile(r"^===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) =====$")
EXIT_LINE = re.compile(r"^refresh exit code: (-?\d+)$")


def recent_runs(log_path, n=14):
    """[(started, outcome)] newest first. A header with no exit line means
    the run was cut off (laptop slept or shut down mid-run)."""
    if not log_path or not os.path.exists(log_path):
        return []
    runs = []
    with open(log_path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            m = RUN_HEADER.match(line)
            if m:
                runs.append([m.group(1), "did not finish"])
                continue
            m = EXIT_LINE.match(line)
            if m and runs:
                runs[-1][1] = "ok" if m.group(1) == "0" else f"failed (exit {m.group(1)})"
    # The run writing this file has not logged its exit line yet.
    if runs and runs[-1][1] == "did not finish":
        runs[-1][1] = "this run"
    return list(reversed(runs))[:n]


def movers(conn, day, prev, k=5):
    rows = conn.execute(
        "SELECT s.ticker, s.name, a.close, b.close FROM snapshot s "
        "JOIN prices a ON a.ticker = s.ticker AND a.date = ? "
        "JOIN prices b ON b.ticker = s.ticker AND b.date = ? "
        "WHERE a.close > 0 AND b.close > 0 AND s.market_cap >= 2e9",
        (day, prev)).fetchall()
    ranked = sorted(((t, n, c / p - 1, c) for t, n, c, p in rows), key=lambda r: r[2])
    return ranked[::-1][:k], ranked[:k]


def build(db_path, log_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    days = [r[0] for r in conn.execute(
        "SELECT date FROM prices GROUP BY date HAVING COUNT(*) >= 100 "
        "ORDER BY date DESC LIMIT 2")]
    day = days[0] if days else None
    priced = conn.execute("SELECT COUNT(*) FROM prices WHERE date = ?", (day,)).fetchone()[0]
    companies = conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
    mood = conn.execute(
        "SELECT date, composite, available FROM ("
        "SELECT date, composite, json_extract(components_json, '$.available_of_7') AS available "
        "FROM sentiment ORDER BY date DESC LIMIT 1)").fetchone()

    out = ["# Data status", "",
           f"Written by the nightly refresh at {datetime.now():%Y-%m-%d %H:%M} (local time).",
           "Prices are end-of-day from Yahoo Finance; fundamentals are SEC EDGAR XBRL.", "",
           "| | |", "|---|---|",
           f"| Prices through | {day or 'none'} |",
           f"| Tickers priced that day | {priced:,} |",
           f"| Companies with fundamentals | {companies:,} |"]
    if mood:
        zone = sentiment.zone_for(mood[1])[1]
        out.append(f"| Market mood ({mood[0]}) | {mood[1]:.0f} / 100, {zone} "
                   f"({mood[2]} of 7 components) |")
    out.append("")

    if len(days) == 2:
        up, down = movers(conn, days[0], days[1])
        out += [f"## Biggest moves on {days[0]}", "",
                "Companies with market cap of $2B or more, close vs the previous close.", "",
                "| Ticker | Company | Close | Change |", "|---|---|--:|--:|"]
        for t, n, chg, close in up + down:
            out.append(f"| {t} | {n} | {close:,.2f} | {chg:+.1%} |")
        out.append("")

    runs = recent_runs(log_path)
    if runs:
        out += ["## Recent refresh runs", "", "| Started | Outcome |", "|---|---|"]
        out += [f"| {s} | {o} |" for s, o in runs]
        out.append("")
    conn.close()
    return "\n".join(out), day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="screener.db")
    ap.add_argument("--log")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    text, day = build(a.db, a.log)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(day or "")


if __name__ == "__main__":
    main()
