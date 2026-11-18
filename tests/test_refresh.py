"""
Startup refresh tests -- no network (Yahoo and the SEC are faked). Run:
    python tests/test_refresh.py
"""

import contextlib
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, edgar, ingest, prices, refresh      # noqa: E402
from tests.make_fixtures import write_all, seed_db           # noqa: E402


@contextlib.contextmanager
def patched(obj, **attrs):
    saved = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


def make_db(path=None):
    tmp = tempfile.mkdtemp()
    fixtures = os.path.join(tmp, "fixtures")
    write_all(fixtures)
    path = path or os.path.join(tmp, "refresh.db")
    conn = db.connect(path)
    db.init(conn)
    seed_db(conn, fixtures)
    return conn, path


def _ticker_cik(conn, ticker):
    return conn.execute("SELECT cik FROM companies WHERE ticker=?", (ticker,)).fetchone()[0]


def _submissions(forms):
    """forms: [(form, accession, filing_date)]"""
    return {"filings": {"recent": {
        "form": [f for f, _a, _d in forms],
        "accessionNumber": [a for _f, a, _d in forms],
        "filingDate": [d for _f, _a, d in forms]}}}


# ------------------------------------------------------------------ prices

def test_price_period_reaches_back_past_the_last_stored_bar():
    assert refresh.period_for_gap(0) == "1mo", "even a same-day gap keeps margin to overlap"
    assert refresh.period_for_gap(3) == "1mo"
    assert refresh.period_for_gap(48) == "3mo"
    assert refresh.period_for_gap(400) == "2y"
    assert refresh.period_for_gap(5000) == "max"
    print("  OK  the price window grows with the gap since the last stored bar")


def _fake_batch(calls, rows_for):
    def fake(tickers, period="max", progress=None, on_chunk=None, keep=True):
        calls.append((list(tickers), period))
        chunk = {t: rows_for(t, period) for t in tickers if rows_for(t, period)}
        if on_chunk:
            on_chunk(chunk)
        return chunk if keep else {}
    return fake


def _last_bar(conn, ticker):
    return conn.execute("SELECT date, close FROM prices WHERE ticker=? "
                        "ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()


def test_prices_step_stores_new_bars_with_a_recent_window_only():
    conn, _ = make_db()
    calls = []

    def rows_for(t, period):
        last, close = _last_bar(conn, t)
        return [(t, last, 1, 1, 1, close, 1), (t, "2030-01-02", 1, 1, 1, 50.0, 1)]
    with patched(prices, fetch_yfinance_batch=_fake_batch(calls, rows_for)):
        state, detail = refresh.step_prices(conn, lambda *_: None)
    assert state == "ok", (state, detail)
    assert all(period != "max" for _t, period in calls), calls
    assert conn.execute("SELECT close FROM prices WHERE ticker='MODT' AND date='2030-01-02'"
                        ).fetchone()[0] == 50.0
    print(f"  OK  incremental pull stored the new bar without re-pulling history ({detail})")


def test_prices_step_repulls_history_when_yahoo_readjusted_the_past():
    """A split or dividend moves every past price. The overlap bar no longer
    matches what is stored, so the whole history must be replaced or the
    chart shows a fake cliff where old and new bases meet."""
    conn, _ = make_db()
    calls = []

    def rows_for(t, period):
        last, close = _last_bar(conn, t)
        factor = 0.5 if t == "MODT" else 1.0                    # a 2:1 split, say
        return [(t, last, 1, 1, 1, close * factor, 1)]
    with patched(prices, fetch_yfinance_batch=_fake_batch(calls, rows_for)):
        state, detail = refresh.step_prices(conn, lambda *_: None)
    full = [t for t, period in calls if period == "max"]
    assert full and "MODT" in full[0], calls
    assert "re-adjusted" in detail, detail
    print(f"  OK  a re-adjusted ticker gets its full history again ({detail})")


def test_prices_step_pulls_full_history_for_newly_ingested_unpriced_companies():
    conn, _ = make_db()
    conn.execute("DELETE FROM prices WHERE ticker='LEGC'")
    conn.commit()
    calls = []

    def rows_for(t, period):
        return [(t, "2026-01-02", 1, 1, 1, 9.0, 1)]
    with patched(prices, fetch_yfinance_batch=_fake_batch(calls, rows_for)):
        refresh.step_prices(conn, lambda *_: None)
    assert any(period == "max" and tickers == ["LEGC"] for tickers, period in calls), calls
    print("  OK  an ingested company with no prices yet gets its whole history")


# ----------------------------------------------------------------- filings

def test_a_new_periodic_filing_is_detected():
    conn, _ = make_db()
    cik = _ticker_cik(conn, "MODT")
    latest = conn.execute("SELECT MAX(filed) FROM facts WHERE cik=?", (cik,)).fetchone()[0]
    known = conn.execute("SELECT accn FROM facts WHERE cik=? LIMIT 1", (cik,)).fetchone()[0]
    later = (date.fromisoformat(latest) + timedelta(days=60)).isoformat()
    subs = _submissions([
        ("10-Q", "0000000-99-000001", later),      # new, periodic
        ("8-K", "0000000-99-000002", later),       # not a financial statement
        ("10-Q", known, latest),                   # already held
        ("10-K", "0000000-01-000003", "2001-03-01"),  # ancient, never in facts
    ])
    new = refresh.new_periodic_filings(conn, cik, subs)
    assert [a for a, _f, _d in new] == ["0000000-99-000001"], new
    print("  OK  only an unseen 10-K/10-Q newer than our data counts as new")


def test_filings_step_skips_cleanly_without_an_sec_contact():
    conn, _ = make_db()
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call the SEC"))   # noqa: E731
    with patched(edgar, fetch_submissions=boom):
        state, detail, changed = refresh.step_filings(conn, lambda *_: None, ua=None)
    assert state == "skipped" and changed == [] and "SEC_USER_AGENT" in detail, (state, detail)
    print("  OK  no SEC_USER_AGENT -> skipped with an actionable message, no request made")


def test_filings_step_reingests_only_companies_with_a_new_report():
    conn, _ = make_db()
    target = _ticker_cik(conn, "MODT")
    latest = conn.execute("SELECT MAX(filed) FROM facts WHERE cik=?", (target,)).fetchone()[0]
    later = (date.fromisoformat(latest) + timedelta(days=60)).isoformat()
    ingested, fetched = [], []

    def fake_subs(cik, ua):
        fetched.append(cik)
        if cik == target:
            return _submissions([("10-Q", "0000000-99-000001", later)])
        return _submissions([])

    def fake_ingest(conn_, cik, ua, with_meta=True):
        ingested.append((cik, with_meta))
        return 1
    with patched(edgar, fetch_submissions=fake_subs), patched(ingest, ingest_company=fake_ingest):
        state, detail, changed = refresh.step_filings(conn, lambda *_: None, ua="Test Suite test@example.com")
        assert state == "ok" and changed == [target], (state, detail, changed)
        assert ingested == [(target, False)], "reuse the submissions already fetched (with_meta=False)"
        assert len(fetched) >= 6, "every company should have been looked at"

        fetched.clear()
        state2, detail2, changed2 = refresh.step_filings(conn, lambda *_: None, ua="Test Suite test@example.com")
        assert fetched == [] and changed2 == [], "checked <12h ago: no repeat SEC traffic"
    print(f"  OK  only the company with a new 10-Q is re-ingested; a rerun makes no requests ({detail})")


def test_a_failed_check_is_retried_next_run_not_marked_done():
    conn, _ = make_db()
    with patched(edgar, fetch_submissions=lambda cik, ua: (_ for _ in ()).throw(RuntimeError("503"))):
        state, detail, _changed = refresh.step_filings(conn, lambda *_: None, ua="Test Suite test@example.com")
    assert "failed" in detail, detail
    left = conn.execute("SELECT COUNT(*) FROM filing_check").fetchone()[0]
    assert left == 0, "a failed fetch must not be recorded as checked"
    print("  OK  SEC failures are counted and retried on the next run")


# --------------------------------------------------------------------- run

def test_run_orders_steps_and_renormalizes_only_changed_companies():
    conn, path = make_db()
    conn.close()
    order = []
    normalized = []

    def step(name, ret):
        def fn(conn_, log, *a, **k):
            order.append(name)
            return ret
        return fn

    with patched(refresh,
                 step_prices=step("prices", ("ok", "x")),
                 step_filings=step("filings", ("ok", "1 new", [123]))), \
         patched(refresh.transform, build_snapshot=lambda c: order.append("snapshot") or 7,
                 normalize_all=lambda c, ciks=None, **k: normalized.append(ciks) or 5), \
         patched(refresh.sentiment, compute=lambda c: order.append("sentiment") or None):
        result = refresh.run(db_path=path, ua="Test Suite test@example.com")
    assert order == ["prices", "snapshot", "sentiment", "filings", "snapshot"], order
    assert normalized == [[123]], "only the company that reported is renormalized"
    assert result["state"] == "done" and result["step"] is None
    names = [s["name"] for s in result["steps"]]
    assert names == ["prices", "snapshot", "sentiment", "filings", "normalize",
                     "snapshot (after filings)"], names
    print("  OK  prices -> snapshot -> gauge -> filings -> normalize changed -> snapshot")


def test_run_without_new_filings_skips_the_second_pass():
    conn, path = make_db()
    conn.close()
    order = []
    with patched(refresh,
                 step_prices=lambda c, log: ("ok", "x"),
                 step_filings=lambda c, log, ua: ("ok", "none new", [])), \
         patched(refresh.transform, build_snapshot=lambda c: order.append("snapshot") or 1):
        result = refresh.run(db_path=path, ua=None)
    assert order == ["snapshot"], order
    assert "normalize" not in [s["name"] for s in result["steps"]]
    print("  OK  nothing new from the SEC -> no renormalize, no second snapshot")


def test_one_failing_step_does_not_stop_the_rest():
    conn, path = make_db()
    conn.close()

    def broken(c, log):
        raise RuntimeError("yahoo is down")
    with patched(refresh, step_prices=broken,
                 step_filings=lambda c, log, ua: ("skipped", "no ua", [])):
        result = refresh.run(db_path=path, ua=None)
    states = {s["name"]: s["state"] for s in result["steps"]}
    assert states["prices"] == "error" and states["snapshot"] == "ok" and states["sentiment"] == "ok", states
    assert result["state"] == "done"
    print("  OK  prices failing still leaves the snapshot, gauge and filings check to run")


def test_a_second_run_while_one_is_running_is_ignored():
    conn, path = make_db()
    conn.close()
    refresh._status.update(state="running")
    try:
        called = []
        with patched(refresh, step_prices=lambda c, log: called.append(1) or ("ok", "")):
            refresh.run(db_path=path)
        assert not called
    finally:
        refresh._status.update(state="idle")
    print("  OK  concurrent runs can't overlap")


# --------------------------------------------------------------------- app

def _client(path):
    os.environ["SCREENER_DB"] = path
    os.environ.pop("FINNHUB_API_KEY", None)
    from fastapi.testclient import TestClient
    from web import app as webapp
    os.environ.pop("FINNHUB_API_KEY", None)
    webapp._fresh_cache["at"] = 0.0
    return TestClient(webapp.app), webapp


def test_app_startup_runs_the_refresh_in_the_background():
    _, path = make_db()
    client, webapp = _client(path)
    calls = []
    with patched(refresh, run=lambda **kw: calls.append(kw)):
        os.environ.pop("SCREENER_AUTO_REFRESH", None)
        with client:
            webapp.app.state.refresh_thread.join(timeout=10)
    assert len(calls) == 1 and calls[0]["db_path"] == path, calls
    print("  OK  starting the app kicks off one background refresh")


def test_auto_refresh_can_be_turned_off():
    _, path = make_db()
    client, webapp = _client(path)
    calls = []
    os.environ["SCREENER_AUTO_REFRESH"] = "0"
    try:
        with patched(refresh, run=lambda **kw: calls.append(kw)):
            with client:
                assert webapp.app.state.refresh_thread is None
    finally:
        os.environ.pop("SCREENER_AUTO_REFRESH", None)
    assert not calls
    print("  OK  SCREENER_AUTO_REFRESH=0 starts nothing")


def test_startup_survives_a_refresh_that_raises():
    _, path = make_db()
    client, webapp = _client(path)

    def explode(**kw):
        raise RuntimeError("boom")
    with patched(refresh, run=explode):
        with client:
            webapp.app.state.refresh_thread.join(timeout=10)
            assert client.get("/").status_code == 200
    print("  OK  the site still serves pages if the refresh blows up")


def test_refresh_endpoint_and_footer_show_data_freshness():
    _, path = make_db()
    client, webapp = _client(path)
    os.environ["SCREENER_AUTO_REFRESH"] = "0"
    try:
        with client:
            d = client.get("/api/refresh").json()
            assert "refresh" in d and "freshness" in d, d
            assert d["freshness"]["prices_through"], d
            page = client.get("/stocks").text
            assert "Prices through" in page and d["freshness"]["prices_through"] in page
    finally:
        os.environ.pop("SCREENER_AUTO_REFRESH", None)
    print(f"  OK  /api/refresh and the footer report prices through {d['freshness']['prices_through']}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        print(f"{t.__name__}:")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {e}")
        except Exception as e:                              # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
