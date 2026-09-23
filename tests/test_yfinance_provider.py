"""
yfinance price provider tests -- no network calls. Run:
    python tests/test_yfinance_provider.py

yfinance is an optional dependency (see screener/prices.py's module
docstring); tests that need pandas to build a fake price frame skip
cleanly, rather than failing, when it isn't installed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import prices                                  # noqa: E402

try:
    import pandas as pd
    HAVE_PANDAS = True
except ImportError:
    HAVE_PANDAS = False


def _fake_frame(rows):
    """rows: [(date, open, high, low, close, volume), ...] -> a DataFrame
    shaped like what yf.Ticker(...).history() / yf.download() return."""
    import pandas as pd
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
         "Volume": [r[5] for r in rows]},
        index=idx,
    )


def test_yfinance_not_installed_raises_a_clear_error():
    original = prices._require_yfinance
    prices._require_yfinance = lambda: (_ for _ in ()).throw(
        RuntimeError("yfinance isn't installed. It's optional (pulls in pandas/numpy), "
                     "so it's not in requirements.txt by default -- pip install yfinance"))
    try:
        try:
            prices.fetch_yfinance("AAPL")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "pip install yfinance" in str(e), e
    finally:
        prices._require_yfinance = original
    print("  OK  a missing yfinance install raises a clear, actionable error")


def test_frame_to_rows_matches_the_stooq_eodhd_shape():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    frame = _fake_frame([("2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000.0)])
    rows = prices._rows_from_yf_frame(frame, "aapl")
    assert rows == [("AAPL", "2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000.0)], rows
    print("  OK  yfinance rows match the (ticker,date,open,high,low,close,volume) shape")


def test_frame_to_rows_drops_nan_close_rows():
    """A NaN Close (a halted/no-trade day) must not land in `prices` as a
    row that later code reads as a real (falsy-but-present) price."""
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    import math
    frame = _fake_frame([
        ("2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000.0),
        ("2026-01-03", 10.5, 10.5, 10.5, math.nan, 0.0),
    ])
    rows = prices._rows_from_yf_frame(frame, "AAPL")
    assert len(rows) == 1, rows
    print("  OK  a NaN close (halted day) is dropped, not stored as a fake price")


def test_empty_frame_returns_no_rows():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return
    empty = pd.DataFrame()
    assert prices._rows_from_yf_frame(empty, "NOSUCH") == []
    print("  OK  an empty frame (unresolvable ticker) returns no rows, not an error")


def test_batch_one_bad_ticker_does_not_take_out_the_others():
    """A ticker yfinance can't resolve must be silently absent from the
    result, never abort the whole batch -- one bad symbol out of hundreds
    would otherwise cost every other ticker's data."""
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return

    class FakeYF:
        @staticmethod
        def download(tickers, **kwargs):
            cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
            idx = pd.to_datetime(["2026-01-02"])
            data = pd.DataFrame(index=idx, columns=cols, dtype=float)
            if "GOOD" in tickers:
                data[("GOOD", "Open")] = [10.0]
                data[("GOOD", "High")] = [11.0]
                data[("GOOD", "Low")] = [9.5]
                data[("GOOD", "Close")] = [10.5]
                data[("GOOD", "Volume")] = [1000.0]
            # "BAD" stays all-NaN, like an unresolvable ticker
            return data

    original = prices._require_yfinance
    prices._require_yfinance = lambda: FakeYF
    try:
        out = prices.fetch_yfinance_batch(["GOOD", "BAD"])
        assert "GOOD" in out and out["GOOD"][0][0] == "GOOD", out
        assert "BAD" not in out, out
    finally:
        prices._require_yfinance = original
    print("  OK  an unresolvable ticker is absent from the batch result, GOOD still comes through")


def test_batch_chunks_large_ticker_lists():
    """The real bug found running this for real: a single yf.download() call
    across the whole universe at period='max' built a multi-GB DataFrame and
    never finished. Chunking must actually split the work, not just accept
    a chunk-size parameter that's never used."""
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return

    calls = []

    class FakeYF:
        @staticmethod
        def download(tickers, **kwargs):
            calls.append(list(tickers))
            cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
            idx = pd.to_datetime(["2026-01-02"])
            data = pd.DataFrame(index=idx, columns=cols, dtype=float)
            for t in tickers:
                data[(t, "Open")] = [1.0]
                data[(t, "High")] = [1.0]
                data[(t, "Low")] = [1.0]
                data[(t, "Close")] = [1.0]
                data[(t, "Volume")] = [1.0]
            return data

    original_yf, original_chunk = prices._require_yfinance, prices.YFINANCE_CHUNK_SIZE
    prices._require_yfinance = lambda: FakeYF
    prices.YFINANCE_CHUNK_SIZE = 3
    try:
        tickers = [f"T{i}" for i in range(7)]
        out = prices.fetch_yfinance_batch(tickers)
        assert len(calls) == 3, f"7 tickers at chunk size 3 should be 3 calls, got {len(calls)}"
        assert [len(c) for c in calls] == [3, 3, 1], calls
        assert set(out) == set(tickers), out
    finally:
        prices._require_yfinance = original_yf
        prices.YFINANCE_CHUNK_SIZE = original_chunk
    print(f"  OK  7 tickers at chunk size 3 -> {[len(c) for c in calls]}, all data present")


def test_batch_one_failing_chunk_does_not_take_out_the_rest():
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return

    class FlakyYF:
        calls = 0

        @staticmethod
        def download(tickers, **kwargs):
            FlakyYF.calls += 1
            if FlakyYF.calls == 1:
                raise RuntimeError("simulated network failure on the first chunk")
            cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
            idx = pd.to_datetime(["2026-01-02"])
            data = pd.DataFrame(index=idx, columns=cols, dtype=float)
            for t in tickers:
                for col in ("Open", "High", "Low", "Close", "Volume"):
                    data[(t, col)] = [1.0]
            return data

    original_yf, original_chunk = prices._require_yfinance, prices.YFINANCE_CHUNK_SIZE
    prices._require_yfinance = lambda: FlakyYF
    prices.YFINANCE_CHUNK_SIZE = 2
    try:
        out = prices.fetch_yfinance_batch(["A", "B", "C", "D"])
        assert "A" not in out and "B" not in out, "first (failed) chunk should be absent, not crash the call"
        assert "C" in out and "D" in out, "later chunks must still succeed after an earlier one failed"
    finally:
        prices._require_yfinance = original_yf
        prices.YFINANCE_CHUNK_SIZE = original_chunk
    print("  OK  a failed chunk is skipped, later chunks still come through")


def test_on_chunk_fires_incrementally_not_just_at_the_end():
    """The real reliability fix: a caller storing to disk per chunk must see
    each chunk as it completes, not accumulate silently until the whole
    (possibly very long) run finishes -- otherwise a crash mid-run loses
    everything instead of just what hadn't fetched yet."""
    if not HAVE_PANDAS:
        print("  SKIP  pandas not installed")
        return

    class FakeYF:
        @staticmethod
        def download(tickers, **kwargs):
            cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
            idx = pd.to_datetime(["2026-01-02"])
            data = pd.DataFrame(index=idx, columns=cols, dtype=float)
            for t in tickers:
                for col in ("Open", "High", "Low", "Close", "Volume"):
                    data[(t, col)] = [1.0]
            return data

    seen_chunks = []
    original_yf, original_chunk = prices._require_yfinance, prices.YFINANCE_CHUNK_SIZE
    prices._require_yfinance = lambda: FakeYF
    prices.YFINANCE_CHUNK_SIZE = 2
    try:
        result = prices.fetch_yfinance_batch(
            ["A", "B", "C", "D", "E"], on_chunk=lambda c: seen_chunks.append(set(c)))
        assert len(seen_chunks) == 3, seen_chunks
        assert seen_chunks[0] == {"A", "B"}, seen_chunks
        assert seen_chunks[2] == {"E"}, seen_chunks
        assert set(result) == {"A", "B", "C", "D", "E"}
    finally:
        prices._require_yfinance = original_yf
        prices.YFINANCE_CHUNK_SIZE = original_chunk
    print(f"  OK  on_chunk fired {len(seen_chunks)} times incrementally: {[sorted(c) for c in seen_chunks]}")


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
