"""
Browse taxonomy for /stocks — sectors, size bands, index membership, activity.

Alphabet was never a good primary axis. Nobody thinks "show me companies
starting with M"; they think "show me large-cap financials" or "what's moving
today". Alphabet stays because it's a familiar fallback, but it's one tab
among several rather than the only way in.

Sector comes from the SIC code on the filing. SIC is old and coarse compared
to GICS, but GICS is licensed by S&P and MSCI and costs real money, while SIC
arrives free on every single filing. The division mapping below is the
official SEC one.
"""

# SIC divisions, as published by the SEC. (low, high, label, slug)
SIC_DIVISIONS = [
    (100, 999, "Agriculture & Forestry", "agriculture"),
    (1000, 1499, "Mining & Energy", "mining-energy"),
    (1500, 1799, "Construction", "construction"),
    (2000, 3999, "Manufacturing", "manufacturing"),
    (4000, 4999, "Transport & Utilities", "transport-utilities"),
    (5000, 5199, "Wholesale trade", "wholesale"),
    (5200, 5999, "Retail trade", "retail"),
    (6000, 6799, "Finance & Real estate", "finance"),
    (7000, 8999, "Services", "services"),
    (9100, 9999, "Public administration", "public-admin"),
]

# Manufacturing and Services are enormous and useless as single buckets, so a
# few high-volume sub-ranges get promoted to first-class sectors.
SIC_OVERRIDES = [
    (2833, 2836, "Biotech & Pharma", "biotech-pharma"),
    (3570, 3579, "Computer hardware", "computer-hardware"),
    (3661, 3699, "Electronics & Comms", "electronics"),
    (3674, 3674, "Semiconductors", "semiconductors"),
    (7370, 7379, "Software & IT services", "software"),
    (8000, 8099, "Healthcare services", "healthcare"),
    (6020, 6199, "Banking", "banking"),
    (6311, 6411, "Insurance", "insurance"),
    (6500, 6599, "Real estate", "real-estate"),
    (6798, 6798, "REITs", "reits"),
]

# (slug, label, low, high) — low/high in dollars, None = unbounded
MCAP_BANDS = [
    ("mega", "Mega cap", 200e9, None),
    ("large", "Large cap", 10e9, 200e9),
    ("mid", "Mid cap", 2e9, 10e9),
    ("small", "Small cap", 300e6, 2e9),
    ("micro", "Micro cap", None, 300e6),
]

SORTS = {
    "active": ("Most active", "volume DESC"),
    "mcap": ("Market cap", "market_cap DESC"),
    "gainers": ("Top gainers", "change_pct DESC"),
    "losers": ("Top losers", "change_pct ASC"),
    "pe_low": ("Lowest P/E", "pe ASC"),
    "roe_high": ("Highest ROE", "roe DESC"),
}
DEFAULT_SORT = "active"


def sector_of(sic):
    """(label, slug) for a SIC code. The NARROWEST matching range wins.

    Not first-match: several overrides legitimately overlap. SIC 3674 sits
    inside both Electronics (3661-3699) and Semiconductors (3674-3674), and
    first-match ordering silently filed every chipmaker under Electronics.
    Picking by range width makes the outcome independent of list order, so
    adding a new override can't quietly break an existing one.
    """
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return ("Unclassified", "unclassified")

    matches = [(hi - lo, label, slug)
               for lo, hi, label, slug in SIC_OVERRIDES + SIC_DIVISIONS
               if lo <= code <= hi]
    if not matches:
        return ("Unclassified", "unclassified")
    _, label, slug = min(matches, key=lambda m: m[0])
    return (label, slug)


def sectors(conn):
    """Every sector present in the data, with counts. Empty ones aren't shown."""
    rows = conn.execute(
        "SELECT c.sic, COUNT(*) n FROM snapshot s JOIN companies c USING (cik) "
        "WHERE s.ticker IS NOT NULL GROUP BY c.sic").fetchall()
    agg = {}
    for r in rows:
        label, slug = sector_of(r["sic"])
        entry = agg.setdefault(slug, {"slug": slug, "label": label, "count": 0})
        entry["count"] += r["n"]
    return sorted(agg.values(), key=lambda s: -s["count"])


def sics_for_sector(slug):
    """SIC codes belonging to a sector slug, as (low, high) ranges.

    Filtering by a broad division must EXCLUDE codes that a narrower override
    claims, or "Manufacturing" would also return every semiconductor company
    and the two menu entries would overlap confusingly.
    """
    override = [(lo, hi) for lo, hi, _, s in SIC_OVERRIDES if s == slug]
    if override:
        return override
    division = [(lo, hi) for lo, hi, _, s in SIC_DIVISIONS if s == slug]
    if not division:
        return []
    lo, hi = division[0]
    # Carve out any override ranges that fall inside this division.
    holes = sorted((o_lo, o_hi) for o_lo, o_hi, _, _ in SIC_OVERRIDES
                   if o_lo >= lo and o_hi <= hi)
    out, cursor = [], lo
    for h_lo, h_hi in holes:
        if h_lo > cursor:
            out.append((cursor, h_lo - 1))
        cursor = max(cursor, h_hi + 1)
    if cursor <= hi:
        out.append((cursor, hi))
    return out or division


def mcap_bands(conn):
    out = []
    for slug, label, lo, hi in MCAP_BANDS:
        clauses, params = ["market_cap IS NOT NULL"], []
        if lo is not None:
            clauses.append("market_cap >= ?")
            params.append(lo)
        if hi is not None:
            clauses.append("market_cap < ?")
            params.append(hi)
        n = conn.execute(
            f"SELECT COUNT(*) FROM snapshot WHERE {' AND '.join(clauses)}",
            params).fetchone()[0]
        if n:
            out.append({"slug": slug, "label": label, "count": n})
    return out


def indexes(conn):
    try:
        return [{"slug": r[0], "label": r[1], "count": r[2]} for r in conn.execute(
            "SELECT i.index_slug, i.index_name, COUNT(*) FROM index_members i "
            "JOIN snapshot s ON s.ticker = i.ticker "
            "GROUP BY i.index_slug, i.index_name ORDER BY COUNT(*) DESC")]
    except Exception:                                   # noqa: BLE001
        return []                                       # table not created yet


def browse(conn, sector=None, band=None, index=None, letter=None,
           sort=DEFAULT_SORT, page=1, per_page=50):
    """One query powering every browse view.

    Filters compose, so /stocks?sector=banking&band=large&sort=gainers works
    without a combinatorial explosion of routes.

    `volume` and `change_pct` come from the last close in `prices`. That makes
    "most active" honest as of the last trading day rather than intraday --
    labelled as such in the UI. Intraday volume needs a paid feed; see
    docs/DESIGN-SPEC.md §7.1.
    """
    where, params = ["s.ticker IS NOT NULL"], []

    if sector and sector != "all":
        ranges = sics_for_sector(sector)
        if ranges:
            ors = " OR ".join(["(CAST(c.sic AS INTEGER) BETWEEN ? AND ?)"] * len(ranges))
            where.append(f"({ors})")
            for lo, hi in ranges:
                params += [lo, hi]
        elif sector == "unclassified":
            where.append("(c.sic IS NULL OR c.sic = '')")

    if band:
        for slug, _, lo, hi in MCAP_BANDS:
            if slug == band:
                where.append("s.market_cap IS NOT NULL")
                if lo is not None:
                    where.append("s.market_cap >= ?")
                    params.append(lo)
                if hi is not None:
                    where.append("s.market_cap < ?")
                    params.append(hi)

    join_index = ""
    if index:
        join_index = "JOIN index_members im ON im.ticker = s.ticker AND im.index_slug = ?"
        params.insert(0, index)

    if letter:
        where.append("s.ticker LIKE ?")
        params.append(f"{letter.upper()}%")

    order = SORTS.get(sort, SORTS[DEFAULT_SORT])[1]

    # Latest close per ticker, so activity and day-change come from one pass
    # rather than a correlated subquery per row.
    base = f"""
        FROM snapshot s
        JOIN companies c USING (cik)
        {join_index}
        LEFT JOIN (
            SELECT p.ticker, p.volume, p.close,
                   (p.close / NULLIF(prev.close, 0) - 1) * 100 AS change_pct
            FROM prices p
            JOIN (SELECT ticker, MAX(date) d FROM prices GROUP BY ticker) last
              ON last.ticker = p.ticker AND last.d = p.date
            LEFT JOIN prices prev ON prev.ticker = p.ticker
              AND prev.date = (SELECT MAX(date) FROM prices
                               WHERE ticker = p.ticker AND date < p.date)
        ) px ON px.ticker = s.ticker
        WHERE {' AND '.join(where)}
    """

    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT s.ticker, s.name, c.sic, s.sic_description, s.market_cap,
                   s.price, s.pe, s.roe, s.net_margin,
                   px.volume AS volume, px.change_pct AS change_pct
            {base}
            ORDER BY {order.replace('volume', 'px.volume')
                            .replace('change_pct', 'px.change_pct')
                            .replace('market_cap', 's.market_cap')
                            .replace('ticker', 's.ticker')
                            .replace('name', 's.name')
                            .replace('pe', 's.pe')
                            .replace('roe', 's.roe')} NULLS LAST, s.ticker ASC
            LIMIT ? OFFSET ?""",
        params + [per_page, (max(page, 1) - 1) * per_page],
    ).fetchall()

    return {
        "rows": [dict(r, sector=sector_of(r["sic"])[0]) for r in rows],
        "total": total,
        "page": max(page, 1),
        "pages": max(1, -(-total // per_page)),
    }


def most_active(conn, limit=12):
    """Highest volume on the last trading day we have."""
    return browse(conn, sort="active", per_page=limit)["rows"]
