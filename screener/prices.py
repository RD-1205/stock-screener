"""
End-of-day prices.

EDGAR gives you fundamentals for free but no prices, so every valuation ratio
needs a second source. Two providers are wired up:

  stooq  (default, free, no key)  -- daily OHLCV, split-adjusted, decent US
                                    coverage. Undocumented and unsupported;
                                    fine for a side project, don't build a
                                    business on it.
  eodhd  (~$20/mo)               -- documented, has splits/dividends as
                                    separate endpoints, survivorship-bias-free
                                    delisted tickers. Worth it the moment you
                                    care about backtests.

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
