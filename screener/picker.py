"""
Which unpriced companies get the next slot of the daily EODHD quota.

The free tier is 20 requests/day, so every slot is scarce. Going through
tickers alphabetically spends them on whatever happens to start with 'C'
while CVS, Chevron and Home Depot sit unpriced. This ranks instead by:

  size    Market cap can't be known before a company is priced (it's
          price x shares), but revenue and total assets are already in the
          snapshot for every ingested company. Percentile of the larger of
          the two -- assets so banks/insurers (tiny "revenue" relative to
          their balance sheet) aren't buried.
  news    Whether the company is actually named in today's market headlines
          (screener/news.py) -- the ones people are looking at right now.
  luck    A random jitter, so the queue doesn't just drain strictly
          biggest-first and the site gets some mid/small names mixed in.
"""

import random
import re

W_SIZE, W_NEWS, W_LUCK = 0.55, 0.45, 0.30

# Preferred-share series (CDR-PB) and similar are registered under the same
# CIK as the common stock but aren't what anyone means by "the company".
_PREFERRED = re.compile(r"-P[A-Z]?$")
_NAME_NOISE = {"inc", "corp", "corporation", "co", "company", "ltd", "plc",
               "holdings", "group", "the", "of", "and", "de", "new", "class"}
# Words too common in company names -- or in headlines -- to identify one
# company on their own: "First" matches First Horizon, First Financial...;
# "Washington" is the city, not Washington Trust. These only count as part
# of a two-word name ("General Electric"), never alone. On top of this,
# common_first_words() adds whatever the database itself shows is shared.
_GENERIC = {"first", "general", "american", "united", "national", "community",
            "capital", "southern", "northern", "western", "eastern", "central",
            "pacific", "atlantic", "international", "global", "financial",
            "bancorp", "bancshares", "bankshares", "bank", "energy",
            "industries", "systems", "technologies", "technology", "resources",
            "services", "partners", "trust", "federal", "republic", "peoples",
            "citizens", "farmers", "merchants", "mutual", "commerce", "premier",
            "independent", "world", "washington", "texas", "california",
            "china", "russia", "europe", "london", "america", "states"}

# A ticker only counts when the headline writes it as a ticker. Bare 3-4
# letter tickers collide with ordinary acronyms: CTO (a job title), LNG
# (liquefied natural gas), and so on.
_TICKER_FORM = r"(?:\$|\(|\b(?:NYSE|NASDAQ|NYSEARCA|AMEX)\s*:\s*)"


def _sig_words(name):
    return [w for w in re.findall(r"[A-Za-z]+", name or "")
            if w.lower() not in _NAME_NOISE]


def common_first_words(conn, min_companies=3):
    """First words shared by several company names in the whole database.

    A hand-kept stoplist can't keep up with 8,000 names; the data can.
    """
    counts = {}
    for (name,) in conn.execute("SELECT name FROM companies WHERE name IS NOT NULL"):
        w = _sig_words(name)
        if w:
            counts[w[0].lower()] = counts.get(w[0].lower(), 0) + 1
    return {w for w, n in counts.items() if n >= min_companies}


def _percentiles(values):
    """{ticker: 0..1 percentile}, ties share a rank."""
    ranked = sorted(v for v in values.values() if v is not None)
    n = len(ranked)
    if n <= 1:
        return {t: 0.5 for t in values}
    out = {}
    for t, v in values.items():
        if v is None:
            out[t] = 0.0
        else:
            lo = next(i for i, x in enumerate(ranked) if x >= v)
            out[t] = lo / (n - 1)
    return out


def _mentions(ticker, name, headlines, common=frozenset()):
    """True if a headline names this company.

    Deliberately conservative -- a false "in the news" is worse than a miss,
    because the bonus is what lets a company jump the size queue. Three ways
    to count, all whole-word:
      - the ticker written as one: $CVX, (INTC), NYSE: F
      - the first two significant words of the name as a phrase
        ("General Electric", "Western Digital"), any case
      - the first word alone ("Chevron", "Intel") only if it's distinctive
        (not generic, not shared by other companies) AND capitalised in the
        headline, so the adjective "independent" can't flag Independent Bank
    """
    text = " ".join(headlines)
    if re.search(_TICKER_FORM + re.escape(ticker) + r"\b", text):
        return True
    words = _sig_words(name)
    if not words:
        return False
    if len(words) >= 2 and re.search(
            r"\b" + re.escape(words[0]) + r"\s+" + re.escape(words[1]) + r"\b",
            text, flags=re.IGNORECASE):
        return True
    first = words[0]
    if len(first) >= 5 and first.lower() not in _GENERIC and first.lower() not in common:
        forms = {first.title(), first.upper()}
        return any(re.search(r"\b" + re.escape(f) + r"\b", text) for f in forms)
    return False


def pick(conn, limit=20, headlines=(), rng=None):
    """Top `limit` unpriced tickers as [(ticker, name, reason)], best first."""
    rng = rng or random
    rows = conn.execute(
        "SELECT s.ticker, s.name, s.revenue_ttm, s.total_assets FROM snapshot s "
        "WHERE s.ticker IS NOT NULL "
        "AND s.ticker NOT IN (SELECT DISTINCT ticker FROM prices)"
    ).fetchall()
    rows = [r for r in rows if not _PREFERRED.search(r["ticker"])]
    if not rows:
        return []

    size_basis = {
        r["ticker"]: (max(r["revenue_ttm"] or 0.0, r["total_assets"] or 0.0) or None)
        for r in rows
    }
    size = _percentiles(size_basis)
    common = common_first_words(conn)

    scored = []
    for r in rows:
        t = r["ticker"]
        in_news = _mentions(t, r["name"], headlines, common)
        score = W_SIZE * size[t] + (W_NEWS if in_news else 0.0) + W_LUCK * rng.random()
        reason = f"size p{int(size[t] * 100)}" + (", in the news" if in_news else "")
        scored.append((score, t, r["name"], reason))

    scored.sort(reverse=True)
    return [(t, name, reason) for _s, t, name, reason in scored[:limit]]
