"""
Chart range/fallback tests -- series.fetch()'s windowed-range fallback is the
one piece of this app that silently returns the WRONG thing instead of an
error when it misbehaves (a 5-day request turning into decades of history
draws fine, just lies). Run: python tests/test_series.py
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, series                               # noqa: E402


def make_db():
    conn = db.connect(":memory:")
    db.init(conn)
    return conn


def _insert_daily_history(conn, ticker, start, end):
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:                                   # trading days only
            rows.append((ticker, d.isoformat(), 100.0))
        d += timedelta(days=1)
    conn.executemany(
        "INSERT INTO prices (ticker, date, close) VALUES (?,?,?)", rows)
    conn.commit()


def test_stale_data_does_not_fall_back_to_full_history():
    """The actual bug reported: price ingest hasn't run in 9 days, AAPL has
    45 years of history, and asking for '5d' returned 11,535 points spanning
    1980-2026 instead of a handful of recent days."""
    conn = make_db()
    _insert_daily_history(conn, "AAPL", date(1980, 12, 12), date(2026, 9, 21))
    today = date(2026, 9, 30)                                  # 9 days stale
    points, meta = series.fetch(conn, "AAPL", "5d", today=today)
    assert meta["fell_back"] is False, meta
    assert meta["span_days"] <= 10, meta
    assert points[-1][0] == "2026-09-21", points[-1]
    assert len(points) < 20, (
        f"expected a tight few-day window, got {len(points)} points "
        f"(the fallback-to-everything bug would return thousands)")
    print(f"  OK  stale-but-deep history: '5d' returns {len(points)} points "
          f"ending {points[-1][0]}, not the full {meta['full_span_days']}-day history")


def test_genuinely_sparse_ticker_still_falls_back_to_everything():
    """A brand-new ticker with only one bar has nothing to re-anchor to --
    this is the original fallback's actual intended case, and it must still
    work: show the one bar we have rather than an empty chart."""
    conn = make_db()
    conn.execute(
        "INSERT INTO prices (ticker, date, close) VALUES (?,?,?)",
        ("NEWCO", "2026-09-29", 10.0))
    conn.commit()
    today = date(2026, 9, 30)
    points, meta = series.fetch(conn, "NEWCO", "5d", today=today)
    assert meta["fell_back"] is True, meta
    assert len(points) == 1 and points[0][0] == "2026-09-29", points
    print("  OK  a genuinely sparse ticker still falls back to its one real bar")


def test_fresh_data_is_unaffected():
    """The common case -- ingest ran today -- must still take the fast path
    with no fallback at all."""
    conn = make_db()
    _insert_daily_history(conn, "MSFT", date(2020, 1, 1), date(2026, 9, 30))
    points, meta = series.fetch(conn, "MSFT", "5d", today=date(2026, 9, 30))
    assert meta["fell_back"] is False, meta
    assert meta["span_days"] <= 10, meta
    print(f"  OK  fresh data: '5d' stays a tight window ({meta['span_days']} days), no fallback")


def test_stale_retry_that_is_still_too_thin_cascades_to_full_fallback():
    """If re-anchoring to the latest stored date STILL can't find 2 points
    (e.g. only one bar exists, dated a while ago), it must cascade to the
    real fallback rather than returning a 1-point chart."""
    conn = make_db()
    conn.execute(
        "INSERT INTO prices (ticker, date, close) VALUES (?,?,?)",
        ("THIN", "2026-01-05", 50.0))
    conn.commit()
    points, meta = series.fetch(conn, "THIN", "5d", today=date(2026, 9, 30))
    assert meta["fell_back"] is True, meta
    assert len(points) == 1, points
    print("  OK  a still-too-thin stale retry cascades to the full fallback")


def test_five_day_is_five_trading_bars_not_ten_calendar_days():
    """The reported complaint: '5D' drew a week-and-a-half of daily bars."""
    conn = make_db()
    _insert_daily_history(conn, "AAPL", date(2020, 1, 1), date(2026, 9, 30))
    points, meta = series.fetch(conn, "AAPL", "5d", today=date(2026, 9, 30))
    assert len(points) == 5, [p[0] for p in points]
    assert meta["partial"] is False, "five bars is a complete 5D, not 'all available'"
    print(f"  OK  5D is {len(points)} trading bars: {points[0][0]} .. {points[-1][0]}")


def test_ytd_starts_at_last_years_final_close():
    conn = make_db()
    _insert_daily_history(conn, "AAPL", date(2020, 1, 1), date(2026, 11, 17))
    points, meta = series.fetch(conn, "AAPL", "ytd", today=date(2026, 11, 17))
    assert points[0][0] == "2025-12-31", points[0]
    assert points[-1][0] == "2026-11-17", points[-1]
    assert meta["partial"] is False and meta["fell_back"] is False, meta
    print("  OK  YTD begins at Dec 31 so the year's change is measured from last year's close")


def test_ytd_stays_a_ytd_when_data_is_stale():
    """Same stale-ingest trap as 5D: today is past the last bar."""
    conn = make_db()
    _insert_daily_history(conn, "AAPL", date(2020, 1, 1), date(2026, 11, 10))
    points, meta = series.fetch(conn, "AAPL", "ytd", today=date(2026, 11, 17))
    assert points[0][0] == "2025-12-31" and meta["fell_back"] is False, (points[0], meta)
    print("  OK  YTD with a week of stale data still starts at last year's close")


def test_fetch_intraday_measures_change_from_the_close_before_the_window():
    conn = make_db()
    conn.executemany("INSERT INTO prices (ticker, date, close) VALUES (?,?,?)",
                     [("AAPL", "2026-11-13", 90.0), ("AAPL", "2026-11-14", 100.0),
                      ("AAPL", "2026-11-17", 999.0)])
    conn.commit()
    bars = [["2026-11-17T09:30", 101.0], ["2026-11-17T09:35", 102.0], ["2026-11-17T09:40", 105.0]]
    original = series.intraday.fetch
    series.intraday.fetch = lambda t, r: bars
    try:
        points, meta = series.fetch_intraday(conn, "AAPL", "1d")
    finally:
        series.intraday.fetch = original
    assert meta["baseline"] == 100.0, "must be the close BEFORE the session, not that day's own bar"
    assert meta["prev_close"] == 100.0
    assert abs(meta["change"] - 5.0) < 1e-9 and abs(meta["change_pct"] - 5.0) < 1e-9
    assert meta["intraday"] is True and meta["fell_back"] is False
    print("  OK  intraday change is measured from the previous session's close")


def test_fetch_intraday_returns_none_rather_than_a_one_point_chart():
    conn = make_db()
    original = series.intraday.fetch
    try:
        series.intraday.fetch = lambda t, r: None
        assert series.fetch_intraday(conn, "AAPL", "1d") is None
        series.intraday.fetch = lambda t, r: [["2026-11-17T09:30", 1.0]]
        assert series.fetch_intraday(conn, "AAPL", "1d") is None
    finally:
        series.intraday.fetch = original
    print("  OK  no data or a single bar -> None, so the caller can degrade honestly")


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
        except Exception as e:                                # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
