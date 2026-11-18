"""
The refresh: everything that must be brought up to date whenever the platform
runs, in one place and in dependency order.

    1. prices      latest closes for every company; anything newly ingested and
                   still unpriced gets its full history
    2. snapshot    market cap, P/E, P/B, P/S... recomputed from those prices
    3. sentiment   the market-mood gauge, computed from the same prices
    4. filings     ask the SEC whether anyone has reported since we last
                   looked, and re-ingest just those companies
    5. normalize   rebuild fundamentals for only the companies that changed
    6. snapshot    again, now with the new fundamentals (only if 5 did anything)

Prices come first and the gauge and snapshot right behind them so the
price-driven figures are correct within a minute; the SEC check is the slow,
network-bound part and runs after.

Every step is independent and never raises: a failure is recorded and the run
carries on, because an SEC outage must not stop the gauge from updating.
`status()` reports what is running or has run, for the footer and /api/refresh.
"""

import threading
import time
from datetime import date, datetime, timedelta

from . import db, edgar, ingest, prices, sentiment, transform

# Forms that carry the financial statements, i.e. the ones that change
# fundamentals. 8-K earnings releases are not XBRL-tagged, so they can't.
PERIODIC_FORMS = {"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "20-F", "40-F"}

# How long before a company's filing list is looked at again. Twelve hours keeps
# a restart-happy dev loop from hitting the SEC 800 times an hour while still
# catching a same-day filing.
CHECK_EVERY_HOURS = 12

# A refreshed price that differs from the stored bar for the same date by more
# than this means Yahoo's split/dividend adjustment has moved since we stored
# it, so that ticker's whole history is stale and must be re-pulled.
REBASE_TOLERANCE = 0.002

_PERIODS = [("1mo", 31), ("3mo", 92), ("6mo", 183),
            ("1y", 366), ("2y", 731), ("5y", 1827)]

_lock = threading.RLock()
_status = {"state": "idle", "step": None, "started": None,
           "finished": None, "steps": []}


def status():
    with _lock:
        return {**_status, "steps": [dict(s) for s in _status["steps"]]}


def _set(**kw):
    with _lock:
        _status.update(kw)


def _record(name, state, detail, seconds):
    with _lock:
        _status["steps"].append({"name": name, "state": state,
                                 "detail": detail, "seconds": round(seconds, 1)})


FILING_CHECK_DDL = """
CREATE TABLE IF NOT EXISTS filing_check (
    cik         INTEGER PRIMARY KEY,
    checked_at  TEXT NOT NULL,
    new_filings INTEGER NOT NULL DEFAULT 0
)"""


# ---------------------------------------------------------------- prices

def period_for_gap(days):
    """Smallest yfinance period that reaches back past the last stored bar
    with margin to overlap it (the overlap is what detects re-adjustment)."""
    need = max(days, 0) + 10
    for name, reach in _PERIODS:
        if reach >= need:
            return name
    return "max"


def _price_tickers(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM companies WHERE ticker IS NOT NULL "
        "AND cik IN (SELECT cik FROM facts) ORDER BY ticker")]


def _rebased(conn, rows):
    """True when the first overlapping bar disagrees with what we stored."""
    for _t, d, _o, _h, _l, close, _v in rows[:6]:
        stored = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date = ?",
            (rows[0][0], d)).fetchone()
        if stored and stored[0]:
            return close is not None and abs(close / stored[0] - 1) > REBASE_TOLERANCE
    return False


def step_prices(conn, log):
    tickers = _price_tickers(conn)
    if not tickers:
        return "skipped", "no ingested companies"
    try:
        have = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices")}
        newest = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    except Exception:                                        # noqa: BLE001
        have, newest = set(), None
    unpriced = [t for t in tickers if t not in have]
    known = [t for t in tickers if t in have]

    stored = {"bars": 0}
    rebase = []

    def store_recent(chunk):
        for t, rows in chunk.items():
            if _rebased(conn, rows):
                rebase.append(t)
            stored["bars"] += prices.store(conn, rows)

    def store_full(chunk):
        for rows in chunk.values():
            stored["bars"] += prices.store(conn, rows)

    if known:
        gap = (date.today() - date.fromisoformat(newest)).days if newest else 9999
        period = period_for_gap(gap)
        log(f"  prices: {len(known)} tickers, period {period}")
        prices.fetch_yfinance_batch(known, period=period, on_chunk=store_recent, keep=False)
    if rebase:
        log(f"  prices: {len(rebase)} tickers re-adjusted by Yahoo, re-pulling full history")
        prices.fetch_yfinance_batch(rebase, period="max", on_chunk=store_full, keep=False)
    if unpriced:
        log(f"  prices: {len(unpriced)} unpriced tickers, pulling full history")
        prices.fetch_yfinance_batch(unpriced, period="max", on_chunk=store_full, keep=False)

    detail = f"{stored['bars']:,} bars"
    if rebase:
        detail += f", {len(rebase)} re-adjusted"
    if unpriced:
        detail += f", {len(unpriced)} newly priced"
    return "ok", detail


# --------------------------------------------------------------- filings

def new_periodic_filings(conn, cik, submissions):
    """Periodic filings the SEC lists for this company that we don't hold.

    Only filings on or after the newest one in `facts` count. Older ones that
    are missing (pre-XBRL, or carrying none of the tags we keep) will never
    appear in `facts` no matter how often we fetch, and treating them as new
    would re-ingest nearly every company on every run.
    """
    recent = (submissions or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    filed = recent.get("filingDate") or []
    latest = conn.execute("SELECT MAX(filed) FROM facts WHERE cik = ?", (cik,)).fetchone()[0]
    if not latest:
        return []
    known = {r[0] for r in conn.execute(
        "SELECT DISTINCT accn FROM facts WHERE cik = ?", (cik,))}
    return [(a, f, d) for f, a, d in zip(forms, accns, filed)
            if f in PERIODIC_FORMS and d >= latest and a not in known]


def _due_ciks(conn, max_age_hours):
    conn.execute(FILING_CHECK_DDL)
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT f.cik FROM facts f LEFT JOIN filing_check c ON c.cik = f.cik "
        "WHERE c.checked_at IS NULL OR c.checked_at < ? ORDER BY f.cik", (cutoff,))]


def step_filings(conn, log, ua, max_age_hours=CHECK_EVERY_HOURS):
    """Returns (state, detail, changed_ciks)."""
    if not ua or "@" not in ua:
        return ("skipped",
                "no SEC contact configured -- set SEC_USER_AGENT (in .env or the "
                "environment); the SEC requires one on every request", [])
    due = _due_ciks(conn, max_age_hours)
    if not due:
        return "ok", f"all companies checked within {max_age_hours}h", []
    log(f"  filings: checking {len(due)} companies")

    changed, failed = [], 0
    for i, cik in enumerate(due, 1):
        try:
            subs = edgar.fetch_submissions(cik, ua)
            new = new_periodic_filings(conn, cik, subs)
            if new:
                ingest.ingest_company(conn, cik, ua, with_meta=False)
                changed.append(cik)
            conn.execute(
                "INSERT OR REPLACE INTO filing_check (cik, checked_at, new_filings) VALUES (?,?,?)",
                (cik, datetime.utcnow().isoformat(timespec="seconds"), len(new)))
            conn.commit()
        except Exception:                                    # noqa: BLE001
            failed += 1                                      # not marked checked, so retried next run
        if i % 100 == 0:
            log(f"  filings: {i}/{len(due)}  {len(changed)} with new reports")
    detail = f"{len(due)} checked, {len(changed)} with new reports"
    if failed:
        detail += f", {failed} failed (will retry)"
    return "ok", detail, changed


# ------------------------------------------------------------------- run

def _timed(name, fn, db_path, log):
    t0 = time.monotonic()
    _set(step=name)
    conn = db.connect(db_path)
    try:
        out = fn(conn)
        state, detail = out[0], out[1]
        extra = out[2] if len(out) > 2 else None
    except Exception as e:                                   # noqa: BLE001
        state, detail, extra = "error", f"{type(e).__name__}: {e}"[:300], None
    finally:
        conn.close()
    _record(name, state, detail, time.monotonic() - t0)
    log(f"{name}: {state} -- {detail}")
    return extra


def _snapshot(conn):
    return "ok", f"{transform.build_snapshot(conn):,} companies"


def _sentiment(conn):
    row = sentiment.compute(conn)
    if not row:
        return "skipped", "not enough priced companies"
    sentiment.store(conn, row)
    zone = sentiment.zone_for(row["composite"])[1]
    return "ok", f"{row['composite']:.0f} ({zone})"


def run(db_path=None, ua=None, log=lambda *_: None):
    """Run every step once, in order. Never raises."""
    with _lock:
        if _status["state"] == "running":
            return status()
        _status.update(state="running", step=None, steps=[],
                       started=datetime.utcnow().isoformat(timespec="seconds"),
                       finished=None)
    try:
        _timed("prices", lambda c: step_prices(c, log), db_path, log)
        _timed("snapshot", _snapshot, db_path, log)
        _timed("sentiment", _sentiment, db_path, log)

        changed = _timed("filings", lambda c: step_filings(c, log, ua), db_path, log) or []
        if changed:
            _timed("normalize",
                   lambda c: ("ok", f"{transform.normalize_all(c, ciks=changed):,} rows "
                                    f"for {len(changed)} companies"),
                   db_path, log)
            _timed("snapshot (after filings)", _snapshot, db_path, log)
    finally:
        _set(state="done", step=None,
             finished=datetime.utcnow().isoformat(timespec="seconds"))
    return status()
