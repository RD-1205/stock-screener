"""
End-of-day prices.

EDGAR gives you fundamentals for free but no prices, so every valuation ratio
needs a second source. Three providers are wired up:

  stooq     (default, free, no key)  -- daily OHLCV, split-adjusted, decent
                                       US coverage. Undocumented and
                                       unsupported; fine for a side project,
                                       don't build a business on it.
  eodhd     (~$20/mo)                -- documented, has splits/dividends as
                                       separate endpoints, survivorship-bias-
                                       free delisted tickers. Worth it the
                                       moment you care about backtests.
  yfinance  (free, no key)           -- scrapes Yahoo Finance's own charting
                                       endpoints via the `yfinance` package.
                                       Verified 2026-09-22 against this
                                       project's own 805-ticker universe:
                                       full history back to each company's
                                       listing (not capped at ~1yr like
                                       EODHD's free tier), and a batched
                                       `yf.download()` pulled all 805 tickers
                                       x 5 years in ~25s, twice in a row, no
                                       throttling observed. Same caveat as
                                       stooq -- unofficial, Yahoo can change
                                       or block it without notice (it's
                                       happened to this exact class of
                                       scraper before, see stooq above) -- so
                                       treat it as free backfill, not a
                                       provider to build a paid product on.

`yfinance` is intentionally NOT a hard dependency (it pulls in pandas,
numpy, lxml and more -- this module's own docstring used to promise the
pipeline needs nothing but stdlib, and that's still true for stooq/eodhd).
It's imported lazily, only inside the two functions that need it, so
`pip install us-screener` with no extras still works for everything else.

Adjusted vs unadjusted matters more than people expect: if your price history
isn't split-adjusted, every chart shows fake -50% crashes and every long-run
return calc is wrong.
"""

import csv
import io
import os
import re
import urllib.request

STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}.us&i=d"
# Without `from`, EODHD defaults to roughly the last year of bars -- one call
# either way, so ask for everything. 1990-01-01 predates every US ticker this
# project will ever ingest.
EODHD_URL = ("https://eodhd.com/api/eod/{sym}.US?api_token={key}&fmt=csv"
            "&period=d&from=1990-01-01")


def _get_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_stooq(ticker):
    text = _get_text(STOOQ_URL.format(sym=ticker.lower()))
    return _parse_ohlcv_csv(text, ticker)


def fetch_eodhd(ticker, api_key=None):
    key = api_key or os.environ.get("EODHD_API_KEY")
    if not key:
        raise RuntimeError("set EODHD_API_KEY")
    text = _get_text(EODHD_URL.format(sym=ticker.upper(), key=key))
    return _parse_ohlcv_csv(text, ticker)


def _require_yfinance():
    try:
        import yfinance
    except ImportError as e:
        raise RuntimeError(
            "yfinance isn't installed. It's optional (pulls in pandas/numpy), "
            "so it's not in requirements.txt by default -- pip install yfinance"
        ) from e
    return yfinance


def fetch_yfinance(ticker, period="max"):
    """One ticker. For backfilling many at once, fetch_yfinance_batch is
    dramatically faster (one HTTP round trip instead of one per ticker) --
    prefer it whenever you're not fetching a single specific symbol."""
    yf = _require_yfinance()
    hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    return _rows_from_yf_frame(hist, ticker)


# yf.download() builds ONE combined DataFrame spanning every ticker's full
# date range at once. That's fine at the sizes tested directly (300
# tickers x 1y, 805 x 5y) but a full-universe period="max" call blew up to
# 3+GB of RAM and never finished -- decades of daily bars, unioned across
# 805 tickers, is a much wider matrix than it looks. Chunking keeps each
# call's matrix small regardless of how many tickers the caller passes.
YFINANCE_CHUNK_SIZE = 100


def fetch_yfinance_batch(tickers, period="max", progress=None, on_chunk=None):
    """Many tickers via yfinance's own batching, chunked to bound memory.

    Returns {ticker: rows}, one entry per ticker that returned any data.
    A ticker yfinance couldn't resolve is simply absent, not an error --
    one bad symbol must never take out the other 804, and now neither does
    one bad chunk. `progress(done, total)` fires after each chunk, for a
    run large enough that silence until the very end would look hung.

    `on_chunk({ticker: rows})` fires after each chunk too, with JUST that
    chunk's results -- the CLI uses this to store to the DB incrementally
    instead of holding all 805 companies' full history in memory until the
    very last ticker resolves. Without it, killing (or crashing) a
    long-running full-universe backfill at 90% loses 100% of the progress;
    with it, whatever finished before the interruption is already saved.
    The full accumulated dict is still returned, for callers (and tests)
    that just want one result at the end.
    """
    tickers = list(tickers)
    out = {}
    for start in range(0, len(tickers), YFINANCE_CHUNK_SIZE):
        chunk = tickers[start:start + YFINANCE_CHUNK_SIZE]
        try:
            chunk_result = _fetch_yfinance_chunk(chunk, period)
        except Exception:                                # noqa: BLE001
            chunk_result = {}  # this chunk failed; the rest still get a chance
        out.update(chunk_result)
        if on_chunk:
            on_chunk(chunk_result)
        if progress:
            progress(min(start + YFINANCE_CHUNK_SIZE, len(tickers)), len(tickers))
    return out


def _fetch_yfinance_chunk(tickers, period):
    yf = _require_yfinance()
    data = yf.download(tickers, period=period, group_by="ticker",
                       progress=False, threads=True, auto_adjust=True)

    out = {}
    multi = hasattr(data.columns, "levels")  # single-ticker download is flat
    for ticker in tickers:
        try:
            frame = data[ticker] if multi else data
            rows = _rows_from_yf_frame(frame, ticker)
        except Exception:                                # noqa: BLE001
            rows = []
        if rows:
            out[ticker] = rows
    return out


def _rows_from_yf_frame(frame, ticker):
    if frame is None or frame.empty:
        return []
    rows = []
    for date, r in frame.iterrows():
        close = r.get("Close")
        if close is None or close != close:              # NaN check, no numpy import needed
            continue
        rows.append((
            ticker.upper(), date.strftime("%Y-%m-%d"),
            _f(r.get("Open")), _f(r.get("High")), _f(r.get("Low")),
            _f(close), _f(r.get("Volume")),
        ))
    return rows


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_ohlcv_csv(text, ticker):
    """Both providers emit Date,Open,High,Low,Close,(Adj Close),Volume.

    EODHD appends a plain-text line when a free-tier date range gets
    truncated ("Data is limited by one year..."), which DictReader happily
    parses as a row with that message sitting in the date column. Validate
    the shape of "date" rather than just checking it's non-empty, or that
    line ends up stored in `prices` as a row with a garbage date.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        norm = {k.strip().lower().replace(" ", "_"): v for k, v in row.items() if k}
        if not norm.get("date") or not _DATE_RE.match(norm["date"]):
            continue
        close = norm.get("adjusted_close") or norm.get("adj_close") or norm.get("close")
        try:
            rows.append((
                ticker.upper(), norm["date"],
                _f(norm.get("open")), _f(norm.get("high")),
                _f(norm.get("low")), _f(close), _f(norm.get("volume")),
            ))
        except (TypeError, ValueError):
            continue
    return rows


def _f(v):
    if v in (None, "", "N/A", "null"):
        return None
    return float(v)


def store(conn, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker,date,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)
