"""
The screening query engine.

screener.in's killer feature is that you type `ROE > 20 AND PE < 15` in a box
and it runs. Doing that safely means you cannot concatenate user input into
SQL -- you need a parser with an allowlist of columns and operators, emitting
parameterized SQL. That's what this is.

Deliberately small. It handles comparisons joined by AND/OR, which covers
~95% of real screens. Add parentheses and arithmetic (`market_cap / revenue`)
later if users ask.
"""

import re
import time

# Allowlist. If a name isn't here it cannot reach the database -- this single
# dict is your entire SQL-injection defense, so keep it exhaustive and typed.
COLUMNS = {
    "price": "price",
    "market_cap": "market_cap",
    "mcap": "market_cap",
    "revenue": "revenue_ttm",
    "net_income": "net_income_ttm",
    "operating_income": "operating_income_ttm",
    "eps": "eps_ttm",
    "fcf": "fcf_ttm",
    "ocf": "ocf_ttm",
    "total_assets": "total_assets",
    "total_equity": "total_equity",
    "total_debt": "total_debt",
    "cash": "cash",
    "pe": "pe",
    "pb": "pb",
    "ps": "ps",
    "ev": "ev",
    "ev_ebit": "ev_to_ebit",
    "roe": "roe",
    "roa": "roa",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "debt_to_equity": "debt_to_equity",
    "current_ratio": "current_ratio",
    "revenue_growth": "revenue_cagr_3y",
    "eps_growth": "eps_cagr_3y",
}

OPERATORS = {">=", "<=", "!=", ">", "<", "="}

# Multipliers so users can write "market_cap > 10b" instead of counting zeros.
SUFFIXES = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "cr": 1e7}

_CONDITION = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|!=|>|<|=)\s*(-?[\d.]+)\s*([a-zA-Z]*)\s*$"
)


class QueryError(ValueError):
    pass


def parse_number(num, suffix):
    try:
        value = float(num)
    except ValueError as e:
        raise QueryError(f"not a number: {num!r}") from e
    if suffix:
        mult = SUFFIXES.get(suffix.lower())
        if mult is None:
            raise QueryError(f"unknown suffix {suffix!r}; use k/m/b/t")
        value *= mult
    return value


def compile_query(text):
    """'roe > 20 and pe < 15' -> ('roe > ? AND pe < ?', [20.0, 15.0])"""
    if not text or not text.strip():
        return "1=1", []

    # Split on AND/OR while keeping the joiners.
    tokens = re.split(r"\s+(and|or)\s+", text.strip(), flags=re.IGNORECASE)
    clauses, params = [], []

    for i, tok in enumerate(tokens):
        if i % 2 == 1:                      # joiner
            clauses.append(tok.upper())
            continue
        m = _CONDITION.match(tok)
        if not m:
            raise QueryError(f"can't parse condition: {tok!r}")
        name, op, num, suffix = m.groups()
        col = COLUMNS.get(name.lower())
        if col is None:
            raise QueryError(
                f"unknown field {name!r}. available: {', '.join(sorted(COLUMNS))}"
            )
        if op not in OPERATORS:
            raise QueryError(f"unknown operator {op!r}")
        clauses.append(f"{col} {op} ?")     # col is allowlisted, value is bound
        params.append(parse_number(num, suffix))

    return " ".join(clauses), params


def compile_ranges(ranges):
    """{'roe': {'min': '15'}, 'pe': {'max': '25'}} -> 'roe >= 15 and pe <= 25'

    This is the range-filter UI's compile step (P4 in docs/PENDING-CHANGES.md):
    it emits DSL *text*, not SQL, so the result is parsed by the same
    compile_query() a hand-typed query goes through -- suffix handling
    ('1b', '500m') and the injection allowlist only exist in one place.

    Both min and max blank means the metric isn't filtered at all, not
    "0 to infinity" -- it's simply left out of the string. Sorted by name so
    the same selections always compile to the same string (a stable, diffable,
    shareable URL).
    """
    clauses = []
    for name in sorted(ranges):
        bounds = ranges[name] or {}
        lo = str(bounds.get("min") or "").strip()
        hi = str(bounds.get("max") or "").strip()
        if lo:
            clauses.append(f"{name} >= {lo}")
        if hi:
            clauses.append(f"{name} <= {hi}")
    return " and ".join(clauses)


# Grouped for the "+ Add metric" menu. A metric can appear in more than one
# group if it's genuinely relevant to both (market_cap is both a valuation
# anchor and the literal definition of "size").
METRIC_LABELS = {
    "price": "Price", "market_cap": "Market cap", "revenue": "Revenue",
    "net_income": "Net income", "operating_income": "Operating income",
    "eps": "EPS", "fcf": "Free cash flow", "ocf": "Operating cash flow",
    "total_assets": "Total assets", "total_equity": "Total equity",
    "total_debt": "Total debt", "cash": "Cash",
    "pe": "P/E", "pb": "P/B", "ps": "P/S", "ev": "EV", "ev_ebit": "EV/EBIT",
    "roe": "ROE %", "roa": "ROA %", "gross_margin": "Gross margin %",
    "operating_margin": "Operating margin %", "net_margin": "Net margin %",
    "debt_to_equity": "Debt/Equity", "current_ratio": "Current ratio",
    "revenue_growth": "Revenue growth % (3y)", "eps_growth": "EPS growth % (3y)",
}
METRIC_GROUPS = [
    ("Valuation", ["pe", "pb", "ps", "ev", "ev_ebit", "market_cap"]),
    ("Quality", ["roe", "roa", "gross_margin", "operating_margin", "net_margin",
                 "debt_to_equity", "current_ratio"]),
    ("Growth", ["revenue_growth", "eps_growth"]),
    ("Size", ["market_cap", "revenue", "net_income", "operating_income", "eps",
              "fcf", "ocf", "total_assets", "total_equity", "total_debt",
              "cash", "price"]),
]
DEFAULT_RANGE_METRICS = ["pe", "roe", "market_cap"]

# How to format a metric's placeholder range in the UI -- money (with B/M/K
# suffixes), percent, or a plain number. Anything not listed here is 'num'.
_MONEY_METRICS = {"market_cap", "revenue", "net_income", "operating_income",
                   "fcf", "ocf", "total_assets", "total_equity",
                   "total_debt", "cash", "ev"}
_PCT_METRICS = {"roe", "roa", "gross_margin", "operating_margin", "net_margin",
                 "revenue_growth", "eps_growth"}
METRIC_FORMAT = {
    name: ("money" if name in _MONEY_METRICS else "pct" if name in _PCT_METRICS else "num")
    for name in COLUMNS
}

_ranges_cache = {"at": 0.0, "data": {}}
RANGES_CACHE_TTL = 900  # seconds


def metric_ranges(conn):
    """p5/p95 per allowlisted column, from the live snapshot -- rendered as
    placeholder text ("P/E - typically 5-45") so a user doesn't type a value
    that matches nothing. Cached in-process; a nightly snapshot rebuild is
    the only thing that changes these, so a page view doesn't need to
    recompute six-plus percentile queries every time.
    """
    now = time.monotonic()
    if _ranges_cache["data"] and now - _ranges_cache["at"] < RANGES_CACHE_TTL:
        return _ranges_cache["data"]

    out = {}
    for col in sorted(set(COLUMNS.values())):
        n = conn.execute(
            f"SELECT COUNT(*) FROM snapshot WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        if n < 5:
            continue
        lo_off = max(0, int(n * 0.05))
        hi_off = min(n - 1, int(n * 0.95))
        lo = conn.execute(
            f"SELECT {col} FROM snapshot WHERE {col} IS NOT NULL "
            f"ORDER BY {col} ASC LIMIT 1 OFFSET ?", (lo_off,)
        ).fetchone()[0]
        hi = conn.execute(
            f"SELECT {col} FROM snapshot WHERE {col} IS NOT NULL "
            f"ORDER BY {col} ASC LIMIT 1 OFFSET ?", (hi_off,)
        ).fetchone()[0]
        out[col] = (lo, hi)

    _ranges_cache["at"] = now
    _ranges_cache["data"] = out
    return out


DEFAULT_SELECT = (
    "ticker, name, market_cap, price, pe, pb, roe, "
    "net_margin, debt_to_equity, revenue_cagr_3y"
)


def available_vintages(conn):
    """Historical as_of dates that have been built, newest first."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of FROM snapshot_history ORDER BY as_of DESC"
    )]


def run(conn, query, order_by="market_cap", desc=True, limit=50,
        select=DEFAULT_SELECT, as_of=None):
    """Run a screen. `as_of` switches to the point-in-time table.

    Same query, same arithmetic, different vintage -- so a screen you like
    today can be replayed against 2019 without lookahead bias.
    """
    where, params = compile_query(query)
    order_col = COLUMNS.get(order_by.lower(), order_by)
    if order_col not in set(COLUMNS.values()):
        raise QueryError(f"cannot order by {order_by!r}")

    if as_of:
        table, extra = "snapshot_history", "as_of = ? AND "
        params = [as_of] + params
    else:
        table, extra = "snapshot", ""

    sql = (
        f"SELECT {select} FROM {table} "
        f"WHERE {extra}{where} AND {order_col} IS NOT NULL "
        f"ORDER BY {order_col} {'DESC' if desc else 'ASC'} LIMIT ?"
    )
    return conn.execute(sql, params + [int(limit)]).fetchall()


def format_table(rows):
    if not rows:
        return "(no matches)"
    headers = rows[0].keys()
    data = [[_fmt(r[h]) for h in headers] for r in rows]
    widths = [max(len(str(h)), *(len(d[i]) for d in data)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "-" * len(line)]
    out += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in data]
    return "\n".join(out)


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        if abs(v) >= 1e9:
            return f"{v/1e9:,.2f}B"
        if abs(v) >= 1e6:
            return f"{v/1e6:,.1f}M"
        return f"{v:,.2f}"
    return str(v)
