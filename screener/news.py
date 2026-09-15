"""
Market-moving news for the homepage -- macro, geopolitical, and business
headlines, not just company filings (see docs/DESIGN-SPEC.md section 1.1: no
free API covers this comprehensively, and general market news was the
explicit ask, not a filings-first feed).

Same licensing rule as everywhere else third-party content shows up: headline
+ source + timestamp + link only, linking out to the original article. Never
republish article bodies. Uses Finnhub's free tier -- the same key already
used for delayed quotes in quotes.py, same thin-adapter-with-a-fallback
shape: a dead provider means an empty feed, never a broken homepage.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news?category={category}&token={key}"

_cache = {}
# Headlines don't need to be fresher than this, and Finnhub's free tier
# (60 req/min) is shared with every other feature using the same key.
CACHE_TTL = 600


def _fetch(category, key, timeout=8):
    url = FINNHUB_NEWS_URL.format(category=urllib.parse.quote(category), key=key)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _clean(raw):
    items = []
    for r in raw or []:
        if not r.get("headline") or not r.get("url"):
            continue
        dt = None
        if r.get("datetime"):
            try:
                dt = datetime.fromtimestamp(int(r["datetime"]), tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                dt = None
        items.append({
            "headline": r["headline"],
            "source": r.get("source") or "",
            "url": r["url"],
            "datetime": dt,
        })
    return items


def general(limit=10):
    """Recent market-moving headlines: macro, geopolitical, business.

    Never raises -- a dead provider or a missing key means an empty list,
    which the homepage renders as an honest empty state, not a 500.
    """
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return []

    now = time.monotonic()
    hit = _cache.get("general")
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1][:limit]

    try:
        items = _clean(_fetch("general", key))
    except Exception:                                  # noqa: BLE001
        stale = _cache.get("general")
        return stale[1][:limit] if stale else []

    _cache["general"] = (now, items)
    return items[:limit]
