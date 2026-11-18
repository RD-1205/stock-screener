"""
Price series for charting.

Two jobs: pull a date range out of the `prices` table, and downsample it to
roughly the number of points a chart can actually draw.

Downsampling matters more than it looks. Ten years of daily bars is ~2,500
points being rendered into ~700 pixels — you ship 3.5x the bytes to draw the
same picture, and the client does 3.5x the work laying it out. Bucketing
server-side is a few lines and cuts the payload by more than half.

Bucket choice follows *actual* span, not just the button. Monthly MAX on ten
months of Stooq history made the longest range look worse than 1Y; that was
a lie dressed as a chart.
"""

from datetime import date, timedelta

from . import intraday

# range key -> (days back or None for everything, preferred bucket)
#
# "1d" is not here: stored prices are end-of-day only, so a single day is at
# most one point. 1D and 5D are drawn from intraday bars instead (see
# fetch_intraday); the "5d" entry below is only the end-of-day fallback for
# when those aren't available, trimmed to the last five trading bars.
# "ytd" ignores its days value: the window starts at last year's final close.
RANGES = {
    "5d":  (10,    "day"),
    "1m":  (31,    "day"),
    "6m":  (183,   "day"),
    "ytd": (366,   "day"),
    "1y":  (366,   "day"),
    "5y":  (1827,  "week"),
    "max": (None,  "month"),
}
DEFAULT_RANGE = "1y"
INTRADAY_RANGES = ("1d", "5d")

# Spans below these keep denser buckets even when the button asked for coarser.
_SPAN_DAY_MAX = 400          # ≤ ~13 months → always daily
_SPAN_WEEK_MAX = 1400        # ≤ ~4 years → week, never month


def _bucket_key(iso, bucket):
    """Bucket a YYYY-MM-DD string. Uses ISO week so weeks don't straddle years."""
    if bucket == "day":
        return iso
    d = date.fromisoformat(iso)
    if bucket == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return iso[:7]                       # month


def downsample(rows, bucket):
    """Keep the LAST close in each bucket.

    Last, not average: a weekly average of daily closes is a number that never
    traded, and it visibly smooths away the highs and lows people are looking
    for. Last close in the bucket is a real price on a real day.
    """
    if bucket == "day":
        return rows
    out, seen, pending = [], None, None
    for iso, close in rows:
        key = _bucket_key(iso, bucket)
        if seen is not None and key != seen:
            out.append(pending)
        pending = (iso, close)
        seen = key
    if pending is not None:
        out.append(pending)
    return out


def _span_days(rows):
    if len(rows) < 2:
        return 0
    return (date.fromisoformat(rows[-1][0]) - date.fromisoformat(rows[0][0])).days


def bucket_for_span(span_days, preferred):
    """Demote coarse buckets when the series isn't long enough to need them."""
    if span_days < 2:
        return "day"
    if span_days <= _SPAN_DAY_MAX:
        return "day"
    if span_days <= _SPAN_WEEK_MAX:
        if preferred == "month":
            return "week"
        return preferred if preferred in ("day", "week") else "week"
    return preferred


def available_ranges(span_days):
    """Which range tabs are meaningful given how much history we hold.

    Shorter tabs stay on so a thin history still has somewhere to click;
    5Y greys out until we have enough calendar span that it isn't just 1Y
    redrawn under a different label.
    """
    return {
        "1d": intraday.available(),
        "5d": True,
        "1m": True,
        "6m": span_days >= 45,
        "ytd": True,
        "1y": span_days >= 90,
        "5y": span_days >= _SPAN_DAY_MAX,
        "max": True,
    }


def _cutoff(range_key, anchor):
    """First date of a windowed range, counted back from `anchor`."""
    if range_key == "ytd":
        return date(anchor.year - 1, 12, 31)
    return anchor - timedelta(days=RANGES[range_key][0])


def _full_span(conn, ticker):
    full = conn.execute(
        "SELECT MIN(date), MAX(date) FROM prices "
        "WHERE ticker = ? AND close IS NOT NULL", (ticker.upper(),)
    ).fetchone()
    if full and full[0] and full[1]:
        return (date.fromisoformat(full[1]) - date.fromisoformat(full[0])).days
    return None


def fetch_intraday(conn, ticker, range_key):
    """(points, meta) for 1D / 5D drawn from intraday bars, or None when
    those aren't available (yfinance missing, network down, market data
    empty). The caller decides how to degrade -- nothing is invented here.

    Points are [["YYYY-MM-DDTHH:MM", close], ...] in exchange-local time.
    Change is measured against the last stored close *before* the first
    session shown (the "previous close"), like every other quote page does,
    not against the first bar of the window.
    """
    pts = intraday.fetch(ticker, range_key)
    if not pts or len(pts) < 2:
        return None

    first_day = pts[0][0][:10]
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? AND close IS NOT NULL "
        "AND date < ? ORDER BY date DESC LIMIT 1",
        (ticker.upper(), first_day)).fetchone()
    baseline = row[0] if row else pts[0][1]

    first, last = pts[0][1], pts[-1][1]
    full_span = _full_span(conn, ticker) or 0
    meta = {
        "ticker": ticker.upper(),
        "range": range_key,
        "bucket": "intraday",
        "count": len(pts),
        "span_days": (date.fromisoformat(pts[-1][0][:10])
                      - date.fromisoformat(first_day)).days,
        "full_span_days": full_span,
        "partial": False,
        "fell_back": False,
        "intraday": True,
        "available_ranges": available_ranges(full_span),
        "quote_label": "Intraday, may be delayed",
        "quote_source": "intraday",
        "baseline": baseline,
        "prev_close": baseline if range_key == "1d" else None,
        "first": first, "last": last,
        "first_date": pts[0][0], "last_date": pts[-1][0],
        "change": last - baseline,
        "change_pct": ((last / baseline - 1) * 100.0) if baseline else None,
        "low": min(p[1] for p in pts),
        "high": max(p[1] for p in pts),
    }
    return pts, meta


def fetch(conn, ticker, range_key=DEFAULT_RANGE, today=None):
    """Return (points, meta) for a ticker.

    points is [[iso_date, close], ...] — arrays rather than objects, which is
    roughly 40% smaller over a few thousand rows and costs one map() on the
    client.
    """
    range_key = range_key if range_key in RANGES else DEFAULT_RANGE
    days, preferred = RANGES[range_key]
    as_of = today or date.today()

    sql = ("SELECT date, close FROM prices "
           "WHERE ticker = ? AND close IS NOT NULL")
    params = [ticker.upper()]
    if days is not None:
        cutoff = _cutoff(range_key, as_of)
        sql += " AND date >= ?"
        params.append(cutoff.isoformat())
    sql += " ORDER BY date ASC"

    rows = [(r[0], r[1]) for r in conn.execute(sql, params)]
    fell_back = False

    # A short range on a sparse history can return one point or none, which
    # draws nothing. Two different causes need two different responses:
    #   stale  the ticker has plenty of history, it just hasn't been priced
    #          in the last few days (the daily ingest doesn't always run) --
    #          a window anchored to wall-clock `today` then asks for days
    #          nobody has yet. Re-anchor the SAME window to the ticker's own
    #          latest stored date instead of giving up on it.
    #   sparse the ticker genuinely doesn't have much history at all. Only
    #          now fall back to everything we hold, rather than an empty box.
    # Anchoring to wall-clock `today` instead of the latest stored date used
    # to be harmless when "everything" meant a handful of rows for a thin
    # ticker -- it's not harmless once a ticker has 40+ years of history: a
    # few stale days turned "5d" into "max" for every ticker whose price
    # ingest hadn't run that day.
    if len(rows) < 2 and days is not None:
        latest = conn.execute(
            "SELECT MAX(date) FROM prices WHERE ticker = ? AND close IS NOT NULL",
            (ticker.upper(),)
        ).fetchone()[0]
        if latest and latest < as_of.isoformat():
            cutoff = _cutoff(range_key, date.fromisoformat(latest))
            rows = [(r[0], r[1]) for r in conn.execute(
                "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL "
                "AND date >= ? ORDER BY date ASC", (ticker.upper(), cutoff.isoformat()))]
        if len(rows) < 2:
            rows = [(r[0], r[1]) for r in conn.execute(
                "SELECT date, close FROM prices WHERE ticker = ? "
                "AND close IS NOT NULL ORDER BY date ASC", (ticker.upper(),))]
            fell_back = True

    # The 10-day window above is only there to find the latest bars; "5D"
    # means five trading days, not whatever fits in ten calendar ones.
    if range_key == "5d" and not fell_back:
        rows = rows[-5:]

    # Full-history span drives which tabs are honest, even when this request
    # asked for a windowed slice.
    full_span = _full_span(conn, ticker)
    if full_span is None:
        full_span = _span_days(rows)

    span = _span_days(rows)
    bucket = bucket_for_span(span, preferred)
    points = downsample(rows, bucket)

    # partial: the button promised a longer window than the data can fill,
    # or we had to abandon the window entirely to draw anything.
    # MAX with no day-cutoff still counts as partial when full history is
    # shorter than what "MAX" implies to a reader (~5y+ of prices).
    requested_days = None
    if days is not None and range_key != "5d":
        requested_days = (as_of - _cutoff(range_key, as_of)).days
    partial = fell_back or (
        requested_days is not None and span > 0 and span < requested_days * 0.85
    )
    if range_key == "max" and full_span < _SPAN_WEEK_MAX:
        partial = True

    meta = {
        "ticker": ticker.upper(),
        "range": range_key,
        "bucket": bucket,
        "count": len(points),
        "span_days": span,
        "full_span_days": full_span,
        "partial": partial,
        "fell_back": fell_back,
        "available_ranges": available_ranges(full_span),
        "quote_label": "At close",
        "quote_source": "close",
    }
    if points:
        first, last = points[0][1], points[-1][1]
        meta.update(
            first=first, last=last,
            first_date=points[0][0], last_date=points[-1][0],
            change=last - first,
            change_pct=((last / first - 1) * 100.0) if first else None,
            low=min(p[1] for p in points),
            high=max(p[1] for p in points),
        )
    return points, meta


def apply_quote(points, meta, quote):
    """Overlay a delayed quote onto the series so the chart ends where the
    header does.

    EOD history stays the body of the line; only the tip moves. Label stays
    honest (Delayed 15 min vs At close). No-ops when quote is missing or is
    itself just the last close.
    """
    if not points or not quote:
        return points, meta
    meta = dict(meta)
    meta["quote_label"] = quote.get("label") or meta.get("quote_label") or "At close"
    meta["quote_source"] = quote.get("source") or "close"
    if quote.get("source") != "finnhub" or quote.get("price") is None:
        return points, meta

    price = float(quote["price"])
    tip_date = quote.get("as_of") or meta.get("last_date") or date.today().isoformat()
    if len(tip_date) > 10:
        tip_date = tip_date[:10]

    out = [list(p) for p in points]
    if out[-1][0] >= tip_date:
        out[-1][1] = price
    else:
        out.append([tip_date, price])

    first, last = out[0][1], out[-1][1]
    meta.update(
        last=last,
        last_date=out[-1][0],
        change=last - first,
        change_pct=((last / first - 1) * 100.0) if first else None,
        low=min(p[1] for p in out),
        high=max(p[1] for p in out),
        count=len(out),
        live_tip=True,
    )
    return out, meta


def latest_change(conn, ticker):
    """Last close and the move from the previous close.

    This is what the company header shows, and what the ticker tape will show
    until a delayed-quote provider is configured. It is honestly 'at close',
    not 'live' — labelling matters, see docs/DESIGN-SPEC.md §7.1.
    """
    rows = conn.execute(
        "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 2", (ticker.upper(),)
    ).fetchall()
    if not rows:
        return None
    last_date, last = rows[0][0], rows[0][1]
    prev = rows[1][1] if len(rows) > 1 else None
    return {
        "date": last_date,
        "price": last,
        "change": (last - prev) if prev is not None else None,
        "change_pct": ((last / prev - 1) * 100.0) if prev else None,
        "source": "close",
    }
