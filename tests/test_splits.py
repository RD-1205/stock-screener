"""
Split-adjustment tests (P5 in docs/PENDING-CHANGES.md). Run:
    python tests/test_splits.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, splits, transform                  # noqa: E402

CIK = 900001

# A synthetic 4-for-1 split, same shape as Apple's real Aug-2020 split:
# FY2016/FY2017 already dropped out of the comparative window by the time
# the split happened, so they were never restated and still sit in `facts`
# on the pre-split basis. FY2018/FY2019 were still inside the FY2020 10-K's
# comparative window, so they got rewritten to the post-split basis. FY2020
# was only ever reported once, already post-split.
FACT_ROWS = [
    # (period_start, period_end, filed, accn, val)
    ("2016-01-01", "2016-12-31", "2017-02-15", "A-2016", 11.00),   # never restated
    ("2017-01-01", "2017-12-31", "2018-02-15", "A-2017", 11.50),   # never restated
    ("2018-01-01", "2018-12-31", "2019-02-15", "A-2018-orig", 12.00),
    ("2018-01-01", "2018-12-31", "2020-11-01", "A-2018-restated", 3.00),
    ("2019-01-01", "2019-12-31", "2020-02-15", "A-2019-orig", 13.00),
    ("2019-01-01", "2019-12-31", "2020-11-01", "A-2019-restated", 3.25),
    ("2020-01-01", "2020-12-31", "2021-02-20", "A-2020", 3.50),    # split year, one filing
]


def setup():
    conn = db.connect(":memory:")
    db.init(conn)
    conn.execute(
        "INSERT INTO companies (cik, name) VALUES (?, 'Split Co')", (CIK,)
    )
    conn.executemany(
        "INSERT INTO facts (cik, taxonomy, concept, unit, period_start, "
        "period_end, filed, accn, val) VALUES (?,'us-gaap','EarningsPerShareDiluted',"
        "'USD/shares',?,?,?,?,?)",
        [(CIK, ps, pe, filed, accn, val) for ps, pe, filed, accn, val in FACT_ROWS],
    )
    conn.commit()
    return conn


def test_detect_splits_recovers_the_ratio():
    conn = setup()
    events = splits.detect_splits(conn, CIK)
    assert events == [("2020-11-01", 4.0)], events
    print(f"  OK  detected {events}")


def test_no_false_positive_on_an_unrelated_small_restatement():
    """A same-period restatement that ISN'T a split (e.g. a minor correction)
    must not get flagged just because it's a different number."""
    conn = db.connect(":memory:")
    db.init(conn)
    conn.execute("INSERT INTO companies (cik, name) VALUES (900002, 'NoSplit Co')")
    conn.executemany(
        "INSERT INTO facts (cik, taxonomy, concept, unit, period_start, "
        "period_end, filed, accn, val) VALUES (900002,'us-gaap',"
        "'EarningsPerShareDiluted','USD/shares',?,?,?,?,?)",
        [
            ("2019-01-01", "2019-12-31", "2020-02-15", "B-orig", 2.00),
            ("2019-01-01", "2019-12-31", "2021-02-15", "B-restated", 2.15),  # 1.075x, not a split
        ],
    )
    conn.commit()
    assert splits.detect_splits(conn, 900002) == []
    print("  OK  a 1.075x restatement is not mistaken for a split")


def test_single_period_restatement_is_not_enough_to_call_it_a_split():
    """A real split restates every comparative period a filing carries at
    once. A single restated period landing near a clean ratio -- even on
    both the basic AND diluted tags -- is exactly what an ordinary one-off
    correction can do too (found on real EDGAR data), so it must not be
    enough on its own."""
    conn = db.connect(":memory:")
    db.init(conn)
    conn.execute("INSERT INTO companies (cik, name) VALUES (900003, 'OneOff Co')")
    conn.executemany(
        "INSERT INTO facts (cik, taxonomy, concept, unit, period_start, "
        "period_end, filed, accn, val) VALUES (900003,'us-gaap',?,'USD/shares',"
        "'2011-04-01','2011-06-30',?,?,?)",
        [
            ("EarningsPerShareDiluted", "2011-07-27", "C-orig-d", 0.41),
            ("EarningsPerShareDiluted", "2013-01-30", "C-restated-d", 0.01),
            ("EarningsPerShareBasic", "2011-07-27", "C-orig-b", 0.42),
            ("EarningsPerShareBasic", "2013-01-30", "C-restated-b", 0.02),
        ],
    )
    conn.commit()
    assert splits.detect_splits(conn, 900003) == [], (
        "a single period's restatement (even echoed on basic+diluted) "
        "must not be mistaken for a corroborated split")
    print("  OK  one restated period alone (both EPS tags) is correctly not a split")


def test_factor_after_only_counts_strictly_later_splits():
    events = [("2020-11-01", 4.0)]
    assert splits.factor_after(events, "2019-01-01") == 4.0, "before the split: needs adjusting"
    assert splits.factor_after(events, "2020-11-01") == 1.0, "on the restating filing: already adjusted"
    assert splits.factor_after(events, "2021-01-01") == 1.0, "filed after: already on current basis"
    print("  OK  factor_after applies only to periods filed before the split")


def test_store_and_load_round_trip():
    conn = setup()
    n = splits.refresh_splits(conn, CIK)
    conn.commit()
    assert n == 1
    assert splits.load_splits(conn, CIK) == [("2020-11-01", 4.0)]
    print("  OK  detected split persisted and reloaded from the `splits` table")


def test_current_view_has_no_split_discontinuity():
    """The actual bug: FY2016-FY2020 EPS must read as one smooth series in
    the current (`fundamentals`) view, not jump 3-4x where the split falls."""
    conn = setup()
    splits.refresh_splits(conn, CIK)
    conn.commit()
    transform.normalize_company(conn, CIK, table="fundamentals")
    conn.commit()

    rows = conn.execute(
        "SELECT period_end, val FROM fundamentals WHERE cik=? AND metric='eps_diluted' "
        "AND period_type='FY' ORDER BY period_end", (CIK,),
    ).fetchall()
    vals = {r["period_end"]: r["val"] for r in rows}
    assert vals["2016-12-31"] == 2.75, vals    # 11.00 / 4
    assert vals["2017-12-31"] == 2.875, vals   # 11.50 / 4
    assert vals["2018-12-31"] == 3.00, vals    # already post-split, untouched
    assert vals["2019-12-31"] == 3.25, vals
    assert vals["2020-12-31"] == 3.50, vals

    ordered = [vals[k] for k in sorted(vals)]
    for a, b in zip(ordered, ordered[1:]):
        ratio = max(a, b) / min(a, b)
        assert ratio < 1.5, f"discontinuity remains: {ordered}"
    print(f"  OK  smooth series {ordered}, no split discontinuity")


def test_point_in_time_view_stays_unadjusted():
    """A vintage must show exactly what was on file then -- including the
    same pre/post-split mixing a contemporaneous viewer would have seen.
    Adjustment is a current-view convenience, not historical truth."""
    conn = setup()
    splits.refresh_splits(conn, CIK)
    conn.commit()
    transform.normalize_company(conn, CIK, as_of="2021-06-01", table="fundamentals_pit")
    conn.commit()

    rows = conn.execute(
        "SELECT period_end, val FROM fundamentals_pit WHERE cik=? AND metric='eps_diluted' "
        "AND period_type='FY' ORDER BY period_end", (CIK,),
    ).fetchall()
    vals = {r["period_end"]: r["val"] for r in rows}
    assert vals["2016-12-31"] == 11.00, "point-in-time must show the raw filed value, unadjusted"
    assert vals["2017-12-31"] == 11.50, vals
    assert vals["2018-12-31"] == 3.00, "this period WAS restated by 2021-06-01, so it's correct as-is"
    print(f"  OK  point-in-time view left unadjusted: {vals}")


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
