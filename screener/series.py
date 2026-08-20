"""
Price series for charting.

Two jobs: pull a date range out of the `prices` table, and downsample it to
roughly the number of points a chart can actually draw.

Downsampling matters more than it looks. Ten years of daily bars is ~2,500
points being rendered into ~700 pixels — you ship 3.5x the bytes to draw the
same picture, and the client does 3.5x the work laying it out. Bucketing
server-side is a few lines and cuts the payload by more than half.
"""

from datetime import date, timedelta

# range key -> (days back or None for everything, bucket)
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


def fetch(conn, ticker, range_key=DEFAULT_RANGE, today=None):
    """Return (points, meta) for a ticker.

    points is [[iso_date, close], ...] — arrays rather than objects, which is
    roughly 40% smaller over a few thousand rows and costs one map() on the
    client.
    """
    range_key = range_key if range_key in RANGES else DEFAULT_RANGE
    days, bucket = RANGES[range_key]

    sql = ("SELECT date, close FROM prices "
           "WHERE ticker = ? AND close IS NOT NULL")
    params = [ticker.upper()]
    if days is not None:
        cutoff = (today or date.today()) - timedelta(days=days)
        sql += " AND date >= ?"
        params.append(cutoff.isoformat())
    sql += " ORDER BY date ASC"

    rows = [(r[0], r[1]) for r in conn.execute(sql, params)]

    # A short range on a sparse history can return one point or none, which
    # draws nothing. Fall back to everything we have rather than an empty box.
    if len(rows) < 2 and days is not None:
        rows = [(r[0], r[1]) for r in conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? "
            "AND close IS NOT NULL ORDER BY date ASC", (ticker.upper(),))]
        bucket = "week"

    points = downsample(rows, bucket)

    meta = {
        "ticker": ticker.upper(),
        "range": range_key,
        "bucket": bucket,
        "count": len(points),
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
