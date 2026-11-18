"""
Intraday bar provider tests -- no network calls (yfinance is faked). Run:
    python tests/test_intraday.py

pandas is needed to build a fake price frame; those tests skip cleanly when
it isn't installed, same as test_yfinance_provider.py.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import intraday                               # noqa: E402

try:
    import pandas as pd
    HAVE_PANDAS = True
except ImportError:
    HAVE_PANDAS = False


def _frame(stamps, closes, tz="America/New_York"):
    idx = pd.DatetimeIndex(pd.to_datetime(stamps)).tz_localize(tz)
    return pd.DataFrame({"Close": closes}, index=idx)


def _install_fake_yfinance(frame_for, calls):
    """Put a fake `yfinance` module where `import yfinance` will find it."""
    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period, interval, auto_adjust):
            calls.append((self.sym, period, interval))
            return frame_for(self.sym)

    mod = types.ModuleType("yfinance")
    mod.Ticker = FakeTicker
    original = sys.modules.get("yfinance")
    sys.modules["yfinance"] = mod
    intraday._cache.clear()
    return original


def _restore(original):
    if original is None:
        sys.modules.pop("yfinance", None)
    else:
        sys.modules["yfinance"] = original
    intraday._cache.clear()


def test_points_are_exchange_local_strings_and_drop_nan():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    frame = _frame(["2026-11-17 09:30", "2026-11-17 09:35", "2026-11-17 09:40"],
                   [10.0, float("nan"), 10.5])
    out = intraday._points(frame)
    assert out == [["2026-11-17T09:30", 10.0], ["2026-11-17T09:40", 10.5]], out
    print("  OK  bars become 'YYYY-MM-DDTHH:MM' in exchange time, NaN bars dropped")


def test_other_timezones_are_converted_to_new_york():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    frame = _frame(["2026-11-17 14:30"], [10.0], tz="UTC")
    out = intraday._points(frame)
    assert out == [["2026-11-17T09:30", 10.0]], out
    print("  OK  a UTC index is converted so 14:30Z shows as the 9:30 open")


def test_empty_frame_gives_no_points():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    assert intraday._points(pd.DataFrame()) == []
    assert intraday._points(None) == []
    print("  OK  empty or missing frame -> no points")


def test_unknown_range_is_not_fetched():
    assert intraday.fetch("AAPL", "1y") is None
    print("  OK  only 1d and 5d are intraday ranges")


def test_results_are_cached_within_the_ttl():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    calls = []
    frame = _frame(["2026-11-17 09:30", "2026-11-17 09:35"], [1.0, 2.0])
    original = _install_fake_yfinance(lambda s: frame, calls)
    try:
        a = intraday.fetch("AAPL", "1d")
        b = intraday.fetch("aapl", "1d")
        assert a == b and len(calls) == 1, calls
        intraday.fetch("AAPL", "5d")
        assert len(calls) == 2, "a different range is a different cache entry"
        assert calls[0] == ("AAPL", "1d", "5m") and calls[1] == ("AAPL", "5d", "30m"), calls
    finally:
        _restore(original)
    print("  OK  repeat requests inside the TTL reuse the cache; each range has its own entry")


def test_expired_entries_are_refetched():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    calls = []
    frame = _frame(["2026-11-17 09:30", "2026-11-17 09:35"], [1.0, 2.0])
    original = _install_fake_yfinance(lambda s: frame, calls)
    try:
        intraday.fetch("AAPL", "1d")
        key = ("AAPL", "1d")
        stamped, pts = intraday._cache[key]
        intraday._cache[key] = (stamped - 10_000, pts)       # long past its TTL
        intraday.fetch("AAPL", "1d")
        assert len(calls) == 2, calls
    finally:
        _restore(original)
    print("  OK  a stale cache entry is refetched")


def test_failures_return_none_and_are_not_cached():
    calls = []

    def boom(sym):
        raise RuntimeError("yahoo is down")
    original = _install_fake_yfinance(boom, calls)
    try:
        assert intraday.fetch("AAPL", "1d") is None
        assert intraday.fetch("AAPL", "1d") is None
        assert len(calls) == 2, "a failure must not be remembered as an answer"
    finally:
        _restore(original)
    print("  OK  a failing provider returns None every time instead of caching the failure")


def test_missing_yfinance_reports_unavailable():
    original = sys.modules.get("yfinance")
    sys.modules["yfinance"] = None                           # makes `import yfinance` raise ImportError
    try:
        assert intraday.available() is False
        assert intraday.fetch("AAPL", "1d") is None
    finally:
        if original is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = original
    print("  OK  without yfinance installed: available() is False and fetch returns None")


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
