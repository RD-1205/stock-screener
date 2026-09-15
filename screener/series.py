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

# range key -> (days back or None for everything, preferred bucket)
#
# No "1d" -- prices are end-of-day only (no intraday data), so a single
# calendar day is at most one point. That's not a chart, and faking one would
# violate the "no fake data" rule the rest of this app follows. "5d" is the
# shortest range that's honestly drawable.
RANGES = {
    "5d":  (10,    "day"),
    "1m":  (31,    "day"),
    "6m":  (183,   "day"),
    "1y":  (366,   "day"),
    "5y":  (1827,  "week"),
    "max": (None,  "month"),
}
DEFAULT_RANGE = "1y"

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
        "5d": True,
        "1m": True,
        "6m": span_days >= 45,
        "1y": span_days >= 90,
        "5y": span_days >= _SPAN_DAY_MAX,
        "max": True,
    }


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
        cutoff = as_of - timedelta(days=days)
        sql += " AND date >= ?"
        params.append(cutoff.isoformat())
    sql += " ORDER BY date ASC"

    rows = [(r[0], r[1]) for r in conn.execute(sql, params)]
    fell_back = False

    # A short range on a sparse history can return one point or none, which
    # draws nothing. Fall back to everything we have rather than an empty box.
    if len(rows) < 2 and days is not None:
        rows = [(r[0], r[1]) for r in conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? "
            "AND close IS NOT NULL ORDER BY date ASC", (ticker.upper(),))]
        fell_back = True

    # Full-history span drives which tabs are honest, even when this request
    # asked for a windowed slice.
    full = conn.execute(
        "SELECT MIN(date), MAX(date) FROM prices "
        "WHERE ticker = ? AND close IS NOT NULL", (ticker.upper(),)
    ).fetchone()
    if full and full[0] and full[1]:
        full_span = (date.fromisoformat(full[1]) - date.fromisoformat(full[0])).days
    else:
        full_span = _span_days(rows)

    span = _span_days(rows)
    bucket = bucket_for_span(span, preferred)
    points = downsample(rows, bucket)

    # partial: the button promised a longer window than the data can fill,
    # or we had to abandon the window entirely to draw anything.
    # MAX with no day-cutoff still counts as partial when full history is
    # shorter than what "MAX" implies to a reader (~5y+ of prices).
    requested_days = days if days is not None else None
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
