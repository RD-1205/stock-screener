"""
Market sentiment gauge -- "how is the market feeling", 0-100.

CNN's Fear & Greed Index weights 7 indicators equally, each as a
standard-deviation move from its own recent norm (docs/DESIGN-SPEC.md 7.3).
This computes the 3 that come straight from our own `prices` table:

  momentum  how far the average priced stock sits above/below its own
            trend (our stand-in for "S&P 500 vs its 125-day MA" -- we
            don't have a licensed index series, so this uses the universe
            of tickers we've actually priced instead)
  strength  net share of the universe sitting near a high vs near a low
            over whatever history we have (our stand-in for "52-week highs
            vs lows" -- honest about using "available history", which is
            capped at ~1 year by the current price provider, not a true
            52-week window for every name)
  breadth   advancing vs declining volume on the most recent trading day

The other 4 CNN components (volatility via VIX, junk bond spread, safe
haven demand, put/call ratio) need FRED and CBOE feeds that aren't wired
up -- deliberately not faked. The composite here is an equal-weighted
average of the 3 available components, clearly labelled as partial
everywhere it's shown.

Proper z-scoring ("today vs this metric's own recent distribution") needs
a history of daily readings that doesn't exist yet. Each run appends one
row to `sentiment`, so that history starts accumulating from day one --
until then, each raw ratio is mapped onto 0-100 with a fixed, documented
range rather than a statistical norm. Re-visit once ~60 days of readings
exist.
"""

import json
from datetime import date, datetime

MIN_UNIVERSE = 5          # below this, a reading is noise, not signal
NEAR_PCT = 0.05            # "within 5% of the high/low" counts as near it
MOMENTUM_LOOKBACK_DAYS = 125


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _momentum(conn):
    """Average, across the priced universe, of latest close vs each
    ticker's own moving average -- how far above/below trend, on average.
    """
    rows = conn.execute(
        "SELECT ticker, close, date FROM prices ORDER BY ticker, date"
    ).fetchall()
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r["close"])

    ratios = []
    for ticker, closes in by_ticker.items():
        window = closes[-MOMENTUM_LOOKBACK_DAYS:]
        if len(window) < 10 or closes[-1] is None:
            continue
        avg = sum(window) / len(window)
        if avg:
            ratios.append(closes[-1] / avg)

    if not ratios:
        return None, {"tickers": 0}
    avg_ratio = sum(ratios) / len(ratios)
    # +/-1% average deviation from trend moves the score 5 points either way.
    score = _clamp(50 + (avg_ratio - 1.0) * 500)
    return score, {"avg_ratio": round(avg_ratio, 4), "tickers": len(ratios)}


def _strength(conn):
    """Net share of the universe near its own high vs near its own low,
    over whatever price history we actually have per ticker."""
    rows = conn.execute("SELECT ticker, close FROM prices ORDER BY ticker, date").fetchall()
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r["close"])

    near_high = near_low = total = 0
    for ticker, closes in by_ticker.items():
        closes = [c for c in closes if c is not None]
        if len(closes) < 10:
            continue
        last, hi, lo = closes[-1], max(closes), min(closes)
        total += 1
        if hi and last >= hi * (1 - NEAR_PCT):
            near_high += 1
        if lo and last <= lo * (1 + NEAR_PCT):
            near_low += 1

    if not total:
        return None, {"tickers": 0}
    net = (near_high - near_low) / total
    score = _clamp(50 + net * 50)
    return score, {"near_high": near_high, "near_low": near_low, "tickers": total}


def _breadth(conn):
    """Advancing vs declining volume on the most recent trading day
    common to the priced universe."""
    latest = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    if not latest:
        return None, {"tickers": 0}

    rows = conn.execute(
        "SELECT p.ticker, p.close, p.volume, "
        "  (SELECT close FROM prices q WHERE q.ticker = p.ticker "
        "   AND q.date < p.date ORDER BY q.date DESC LIMIT 1) AS prev_close "
        "FROM prices p WHERE p.date = ?", (latest,)
    ).fetchall()

    adv_vol = decl_vol = 0.0
    n = 0
    for r in rows:
        if r["prev_close"] is None or not r["volume"]:
            continue
        n += 1
        if r["close"] > r["prev_close"]:
            adv_vol += r["volume"]
        elif r["close"] < r["prev_close"]:
            decl_vol += r["volume"]

    total_vol = adv_vol + decl_vol
    if not total_vol or n < MIN_UNIVERSE:
        return None, {"tickers": n, "date": latest}
    score = _clamp(50 + (adv_vol - decl_vol) / total_vol * 50)
    return score, {"advancing_volume": adv_vol, "declining_volume": decl_vol,
                   "tickers": n, "date": latest}


ZONES = [
    (0, 20, "extreme-fear", "Extreme fear"),
    (20, 40, "fear", "Fear"),
    (40, 60, "neutral", "Neutral"),
    (60, 80, "greed", "Greed"),
    (80, 101, "extreme-greed", "Extreme greed"),
]


def zone_for(score):
    for lo, hi, slug, label in ZONES:
        if lo <= score < hi:
            return slug, label
    return ZONES[-1][2], ZONES[-1][3]


def compute(conn):
    """One reading, right now, from whatever's in `prices` today.

    Returns None if the priced universe is too small for any component to
    mean anything -- an honest "not enough data yet" beats a confident
    number computed from 3 tickers.
    """
    universe = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM prices").fetchone()[0]
    if universe < MIN_UNIVERSE:
        return None

    momentum, mom_detail = _momentum(conn)
    strength, str_detail = _strength(conn)
    breadth, brd_detail = _breadth(conn)

    parts = [(n, s) for n, s in
             (("momentum", momentum), ("strength", strength), ("breadth", breadth))
             if s is not None]
    if not parts:
        return None
    composite = sum(s for _, s in parts) / len(parts)

    return {
        "date": date.today().isoformat(),
        "momentum": momentum, "strength": strength, "breadth": breadth,
        "composite": composite,
        "components_json": json.dumps({
            "momentum": mom_detail, "strength": str_detail, "breadth": brd_detail,
            "available_of_7": len(parts),
        }),
        "universe_size": universe,
        "computed_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def store(conn, row):
    conn.execute(
        "INSERT INTO sentiment (date, momentum, strength, breadth, composite, "
        "components_json, universe_size, computed_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET momentum=excluded.momentum, "
        "strength=excluded.strength, breadth=excluded.breadth, "
        "composite=excluded.composite, components_json=excluded.components_json, "
        "universe_size=excluded.universe_size, computed_at=excluded.computed_at",
        (row["date"], row["momentum"], row["strength"], row["breadth"],
         row["composite"], row["components_json"], row["universe_size"],
         row["computed_at"]),
    )
    conn.commit()


def latest(conn):
    row = conn.execute(
        "SELECT * FROM sentiment ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        return None
    out = dict(row)
    out["components"] = json.loads(row["components_json"] or "{}")
    zslug, zlabel = zone_for(row["composite"])
    out["zone_slug"], out["zone_label"] = zslug, zlabel
    return out


def history(conn, limit=90):
    rows = conn.execute(
        "SELECT date, composite FROM sentiment ORDER BY date DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows][::-1]
