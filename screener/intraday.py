"""
Intraday bars for the 1D and 5D chart ranges.

Everything else in this app is end-of-day, stored in `prices`. Intraday bars
are far too many to store (78 five-minute bars per ticker per session) and only
the latest session or week is ever drawn, so they are fetched on demand through
yfinance and held in a short in-process cache instead of the database.

Same caveats as the yfinance daily provider (see prices.py): free, unofficial,
optional dependency, imported lazily. Any failure -- not installed, network,
Yahoo changing something -- returns None and the caller degrades honestly
rather than drawing something invented.
"""

import threading
import time

# range -> (yfinance period, bar interval, cache seconds)
SPECS = {
    "1d": ("1d", "5m", 60),
    "5d": ("5d", "30m", 300),
}

_CACHE_MAX = 300
_cache = {}
_lock = threading.Lock()


def available():
    """True when the optional yfinance dependency is installed."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return False
    return True


def fetch(ticker, range_key):
    """[[ "YYYY-MM-DDTHH:MM", close ], ...] in exchange-local time, or None."""
    spec = SPECS.get(range_key)
    if not spec:
        return None
    period, interval, ttl = spec
    key = (ticker.upper(), range_key)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    try:
        import yfinance as yf
        frame = yf.Ticker(ticker.upper()).history(
            period=period, interval=interval, auto_adjust=True)
        points = _points(frame)
    except Exception:                                        # noqa: BLE001
        return None
    if not points:
        return None

    with _lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now, points)
    return points


def _points(frame):
    if frame is None or frame.empty:
        return []
    idx = frame.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York")
    out = []
    for ts, close in zip(idx, frame["Close"]):
        if close is None or close != close:                  # NaN
            continue
        out.append([ts.strftime("%Y-%m-%dT%H:%M"), float(close)])
    return out
