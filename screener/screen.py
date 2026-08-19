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
