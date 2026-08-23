"""
Market mood gauge tests. Run: python tests/test_sentiment.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, sentiment                          # noqa: E402
from tests.make_fixtures import write_all, seed_db          # noqa: E402

_CACHE = {}


def setup():
    if "conn" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        conn = db.connect(os.path.join(tmp, "sentiment.db"))
        db.init(conn)
        seed_db(conn, fixtures)
        _CACHE["conn"] = conn
    return _CACHE["conn"]


def test_compute_returns_a_bounded_composite():
    conn = setup()
    row = sentiment.compute(conn)
    assert row is not None, "6 priced fixture companies should be enough to compute"
    assert 0 <= row["composite"] <= 100, row["composite"]
    for key in ("momentum", "strength", "breadth"):
        assert row[key] is None or 0 <= row[key] <= 100, (key, row[key])
    print(f"  OK  composite={row['composite']:.1f} from {row['universe_size']} priced companies")


def test_too_small_a_universe_returns_none_not_a_fake_reading():
    """Below MIN_UNIVERSE, an honest 'not enough data' beats a confident
    number computed from a handful of tickers."""
    conn = db.connect(":memory:")
    db.init(conn)
    conn.executemany(
        "INSERT INTO prices (ticker, date, close, volume) VALUES (?,?,?,?)",
        [("AAAA", "2026-01-01", 10.0, 1000), ("AAAA", "2026-01-02", 11.0, 1000)],
    )
    conn.commit()
    assert sentiment.compute(conn) is None
    print("  OK  tiny universe returns None instead of a noisy composite")


def test_zone_labels_cover_the_full_range():
    cases = [(0, "extreme-fear"), (19.9, "extreme-fear"), (20, "fear"),
             (50, "neutral"), (79.9, "greed"), (80, "extreme-greed"), (100, "extreme-greed")]
    for score, expected_slug in cases:
        slug, label = sentiment.zone_for(score)
        assert slug == expected_slug, f"{score} -> {slug}, expected {expected_slug}"
    print("  OK  all 5 zones map correctly across the 0-100 range")


def test_store_and_latest_round_trip():
    conn = setup()
    row = sentiment.compute(conn)
    sentiment.store(conn, row)
    fetched = sentiment.latest(conn)
    assert fetched is not None
    assert abs(fetched["composite"] - row["composite"]) < 1e-6
    assert fetched["zone_slug"] and fetched["zone_label"]
    assert "available_of_7" in fetched["components"]
    print(f"  OK  stored and re-fetched: {fetched['composite']:.1f} ({fetched['zone_label']})")


def test_store_is_idempotent_per_day():
    """Re-running the same day's computation (e.g. a re-triggered nightly
    job) must update the existing row, not accumulate duplicates."""
    conn = setup()
    row = sentiment.compute(conn)
    sentiment.store(conn, row)
    sentiment.store(conn, row)
    n = conn.execute("SELECT COUNT(*) FROM sentiment WHERE date=?", (row["date"],)).fetchone()[0]
    assert n == 1, f"expected exactly one row for {row['date']}, got {n}"
    print("  OK  storing the same day twice updates in place, no duplicate rows")


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
