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
