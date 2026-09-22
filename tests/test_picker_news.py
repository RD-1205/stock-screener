"""
News-mention precision tests for the daily-quota picker. Each case is a real
false positive found running the picker against live headlines.
Run: python tests/test_picker_news.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import picker                                  # noqa: E402


def test_acronym_tickers_do_not_match_as_bare_words():
    """CTO is a job title, LNG is liquefied natural gas."""
    assert not picker._mentions(
        "CTO", "CTO Realty Growth, Inc.",
        ["Disney names CTO for the first time as media giant expands tech push"])
    assert not picker._mentions(
        "LNG", "Cheniere Energy, Inc.",
        ["Hormuz traffic below 10-day average, LNG vessels reappear outside strait"])
    print("  OK  bare CTO / LNG in a headline don't flag Cheniere or CTO Realty")


def test_tickers_count_when_written_as_tickers():
    assert picker._mentions("INTC", "INTEL CORP", ["Chipmaker (INTC) jumps"])
    assert picker._mentions("CVX", "CHEVRON CORP", ["Why $CVX is sliding"])
    assert picker._mentions("F", "FORD MOTOR CO", ["Ford recall hits NYSE: F shares"])
    print("  OK  (INTC), $CVX and NYSE: F all count as a mention")


def test_a_lowercase_adjective_does_not_flag_a_company():
    """'independent monitoring' must not flag Independent Bank Corp."""
    h = ["Russia, China end mandate for independent monitoring of UN sanctions"]
    assert not picker._mentions("INDB", "INDEPENDENT BANK CORP", h)
    print("  OK  'independent' (lowercase adjective) doesn't flag Independent Bank")


def test_a_place_name_does_not_flag_a_company():
    """'Washington Post' / 'in Washington' is the city and the newspaper."""
    h = ["Trump and Xi discuss in Washington next week",
         "Pentagon count, Washington Post reports"]
    assert not picker._mentions("WASH", "WASHINGTON TRUST BANCORP INC", h)
    print("  OK  'Washington' the city doesn't flag Washington Trust Bancorp")


def test_a_distinctive_name_still_matches():
    """The point of all this: real mentions must survive the tightening."""
    assert picker._mentions("INTC", "INTEL CORP",
                            ["Intel and Micron lead our portfolio higher"])
    assert picker._mentions("WEN", "Wendy's Co",
                            ["Wendy's franchisee files for Chapter 11 protection"])
    assert picker._mentions("WDC", "WESTERN DIGITAL CORP",
                            ["Western Digital raises guidance"])
    print("  OK  Intel, Wendy's and Western Digital (a two-word name) still match")


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
