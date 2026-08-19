"""
Intraday quotes for the ticker tape.

Deliberately a thin adapter with a fallback, because the data source here is a
licensing decision as much as a technical one:

  finnhub   15-minute delayed. Free tier, 60 req/min. No per-user exchange
            fees because the data is delayed -- this is why every free
            financial portal runs on delayed quotes.
  close     Last close from our own `prices` table. Always available, always
            honest, needs nothing.

Real-time consolidated quotes are deliberately not an option: they require
agreements with the exchanges plus per-user non-professional fees, which is
not viable pre-revenue. See docs/DESIGN-SPEC.md §7.1.

The `label` on every response exists so the UI can never accidentally present
delayed data as live. Mislabelling it is both a trust problem and a licensing
violation.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

FINNHUB_URL = "https://finnhub.io/api/v1/quote?symbol={sym}&token={key}"

LABELS = {
    "finnhub": "Delayed 15 min",
    "close": "At close",
}

_cache = {}
CACHE_TTL = 45          # seconds; delayed data doesn't change faster than this


def provider():
    """finnhub when a key is present, otherwise last close. No config needed."""
    return "finnhub" if os.environ.get("FINNHUB_API_KEY") else "close"


def label():
    return LABELS[provider()]


def _finnhub_quote(symbol, key, timeout=8):
    url = FINNHUB_URL.format(sym=urllib.parse.quote(symbol.upper()), key=key)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    # Finnhub: c=current, d=change, dp=change %, pc=previous close.
    # An unknown symbol returns zeros rather than a 404, so treat 0 as absent.
    if not d or not d.get("c"):
        return None
    return {
        "price": float(d["c"]),
        "change": float(d.get("d") or 0.0),
        "change_pct": float(d.get("dp") or 0.0),
        "source": "finnhub",
        "label": LABELS["finnhub"],
    }


def get(conn, symbols):
    """Return {symbol: quote} for a list of tickers.

    Never raises. A dead provider degrades to last close rather than taking
    the whole landing page down with it -- a ticker tape is decoration and
    must not be able to break the page it sits on.
    """
    from . import series

    out = {}
    key = os.environ.get("FINNHUB_API_KEY")
    now = time.monotonic()

    for sym in symbols:
        sym = sym.upper()
        hit = _cache.get(sym)
        if hit and now - hit[0] < CACHE_TTL:
            out[sym] = hit[1]
            continue

        quote = None
        if key:
            try:
                quote = _finnhub_quote(sym, key)
            except Exception:                       # noqa: BLE001
                quote = None                        # fall through to close

        if quote is None:
            eod = series.latest_change(conn, sym)
            if eod:
                quote = {
                    "price": eod["price"],
                    "change": eod["change"],
                    "change_pct": eod["change_pct"],
                    "source": "close",
                    "label": LABELS["close"],
                    "as_of": eod["date"],
                }

        if quote:
            _cache[sym] = (now, quote)
            out[sym] = quote

    return out
