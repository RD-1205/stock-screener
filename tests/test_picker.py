"""
Daily-quota picker tests. Run: python tests/test_picker.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, picker                              # noqa: E402


def make_db(rows, priced=()):
    """rows: (ticker, name, revenue_ttm, total_assets)."""
    conn = db.connect(":memory:")
    db.init(conn)
    conn.executemany(
        "INSERT INTO snapshot (cik, ticker, name, revenue_ttm, total_assets) "
        "VALUES (?,?,?,?,?)",
        [(i, t, n, rev, ta) for i, (t, n, rev, ta) in enumerate(rows, 1)],
    )
    conn.executemany("INSERT INTO prices (ticker, date, close) VALUES (?,?,?)",
                     [(t, "2026-09-01", 1.0) for t in priced])
    conn.commit()
    return conn


class NoLuck:
    """Deterministic stand-in for random: no jitter, so ranking is testable."""
    @staticmethod
    def random():
        return 0.0


def test_bigger_companies_come_first_not_alphabetical():
    conn = make_db([
        ("AAA", "ALPHA TINY CORP", 1e6, 1e6),
        ("ZZZ", "ZETA GIANT CORP", 9e11, 1e12),
        ("MMM", "MID SIZED CORP", 5e9, 8e9),
    ])
    got = [t for t, _n, _r in picker.pick(conn, 3, rng=NoLuck)]
    assert got == ["ZZZ", "MMM", "AAA"], got
    print(f"  OK  ranked by size, not by ticker: {got}")


def test_already_priced_and_preferred_shares_are_skipped():
    conn = make_db([
        ("BIG", "BIG CORP", 9e11, 1e12),
        ("HAVE", "ALREADY PRICED CORP", 8e11, 1e12),
        ("CDR-PB", "CEDAR REALTY PREFERRED", 7e11, 1e12),
        ("OK", "OK CORP", 1e9, 1e9),
    ], priced=["HAVE"])
    got = [t for t, _n, _r in picker.pick(conn, 10, rng=NoLuck)]
    assert "HAVE" not in got and "CDR-PB" not in got, got
    assert got == ["BIG", "OK"], got
    print("  OK  priced tickers and preferred-share series never take a slot")


def test_banks_are_ranked_by_assets_not_buried_by_low_revenue():
    conn = make_db([
        ("BANK", "BIG BANK CORP", 2e10, 3e12),      # small revenue, huge balance sheet
        ("MID", "MIDDLE CORP", 2e11, 1e11),
        ("SMALL", "SMALL CORP", 1e8, 1e8),
    ])
    got = [t for t, _n, _r in picker.pick(conn, 3, rng=NoLuck)]
    assert got[0] == "BANK", got
    print("  OK  a bank's balance sheet counts as size, not just its revenue")


def test_news_mention_lets_a_smaller_company_jump_the_queue():
    """News is worth more than a few size percentiles but less than the
    whole ladder -- a headline moves a mid-sized name up, it doesn't put a
    micro-cap ahead of the giants."""
    conn = make_db([
        ("BIGQ", "QUIETONE CORP", 5e11, 5e11),
        ("BIG2", "QUIETTWO CORP", 4e11, 4e11),
        ("HOTT", "HOTTCO INC", 3e11, 3e11),
        ("LOW1", "QUIETTHREE CORP", 2e11, 2e11),
        ("LOW2", "QUIETFOUR CORP", 1e11, 1e11),
    ])
    quiet = [t for t, _n, _r in picker.pick(conn, 5, rng=NoLuck)]
    assert quiet.index("HOTT") == 2, quiet
    hot = picker.pick(conn, 5, headlines=["Hottco shares surge on guidance"], rng=NoLuck)
    assert hot[0][0] == "HOTT" and "in the news" in hot[0][2], hot
    print("  OK  a headline naming the company lifts it above bigger quiet ones")


def test_a_first_word_shared_by_many_companies_is_not_distinctive():
    """The live-data bug: 'World', 'Independent', 'Washington' are ordinary
    words that also start many company names, so a headline containing one
    was flagging World Kinect, Independent Bank, Washington Trust..."""
    conn = make_db([("WKC", "WORLD KINECT CORP", 1e9, 1e9)])
    conn.executemany("INSERT INTO companies (cik, name) VALUES (?,?)",
                     [(100 + i, n) for i, n in enumerate(
                         ["WORLD KINECT CORP", "WORLD ACCEPTANCE CORP",
                          "WORLD WRESTLING INC", "CHEVRON CORP"])])
    common = picker.common_first_words(conn)
    assert "world" in common and "chevron" not in common, common
    h = ["World leaders meet as markets slide"]
    assert not picker._mentions("WKC", "WORLD KINECT CORP", h, common)
    assert picker._mentions("CVX", "CHEVRON CORP", ["Chevron slides"], common)
    print("  OK  'World' (shared by 3 companies) no longer flags World Kinect")


def test_generic_name_words_do_not_count_as_a_news_mention():
    """The bug found in the first draft: 'General' in a headline flagged
    General Mills, General Dynamics and everything else with that word."""
    h = ["General consensus is that rates will rise", "First quarter results are in"]
    assert not picker._mentions("GIS", "GENERAL MILLS INC", h)
    assert not picker._mentions("FHN", "FIRST HORIZON CORP", h)
    assert picker._mentions("GE", "GENERAL ELECTRIC CO",
                            ["General Electric raises outlook"])
    assert picker._mentions("CVX", "CHEVRON CORP", ["Chevron and oil majors slide"])
    print("  OK  'General'/'First' alone don't match; 'General Electric', 'Chevron' do")


def test_short_tickers_only_match_by_name():
    """'F', 'T', 'AN' are ordinary words -- a headline containing them must
    not flag Ford/AT&T/AutoNation."""
    h = ["A rally in T bills as F1 season opens"]
    assert not picker._mentions("F", "FORD MOTOR CO", h)
    assert not picker._mentions("T", "AT&T INC", h)
    assert picker._mentions("F", "FORD MOTOR CO", ["Ford Motor recalls 400k trucks"])
    print("  OK  1-2 letter tickers never match on the ticker alone")


def test_luck_shuffles_but_does_not_overwhelm_size():
    conn = make_db([(f"T{i:02d}", f"COMPANY{i} CORP", float(i + 1) * 1e9, 1e9)
                    for i in range(40)])
    runs = {tuple(t for t, _n, _r in picker.pick(conn, 5, rng=random.Random(s)))
            for s in range(8)}
    assert len(runs) > 1, "different seeds should produce different picks"
    everything_picked = {t for r in runs for t in r}
    assert "T00" not in everything_picked, "the smallest name shouldn't beat a top-tier one"
    print(f"  OK  {len(runs)} distinct pick sets across 8 seeds, none reaching the bottom")


def test_empty_universe_returns_nothing():
    conn = make_db([("X", "X CORP", 1.0, 1.0)], priced=["X"])
    assert picker.pick(conn, 20, rng=NoLuck) == []
    print("  OK  nothing left unpriced -> empty picks, no crash")


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
