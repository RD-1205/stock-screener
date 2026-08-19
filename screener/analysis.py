"""
Per-period statement and ratio tables for the company page.

Everything here reads `fundamentals`, which already holds one row per
(metric, period_end, period_type). The job is to pivot that into the shape a
reader wants: metrics down the side, periods across the top, with growth.

Two conventions worth stating once:

  Annual  -> YoY against the previous fiscal year.
  Quarter -> YoY against the SAME quarter a year earlier, not the previous
             quarter. Most businesses are seasonal, so sequential quarterly
             growth mostly measures the calendar. QoQ is still computed and
             shown as a secondary column, because for some businesses it's the
             number that matters.
"""

from datetime import date

# Income statement / cash flow, in reading order.
FLOW_ROWS = [
    ("revenue", "Revenue", "money"),
    ("cost_of_revenue", "Cost of revenue", "money"),
    ("gross_profit", "Gross profit", "money"),
    ("operating_income", "Operating income", "money"),
    ("net_income", "Net income", "money"),
    ("eps_diluted", "EPS (diluted)", "num"),
    ("operating_cash_flow", "Operating cash flow", "money"),
    ("capex", "Capex", "money"),
]

# Balance sheet items are instants, so they never carry a period_type of FY/Q.
STOCK_ROWS = [
    ("total_assets", "Total assets", "money"),
    ("total_equity", "Total equity", "money"),
    ("total_liabilities", "Total liabilities", "money"),
    ("cash", "Cash", "money"),
    ("long_term_debt", "Long-term debt", "money"),
]

RATIO_ROWS = [
    ("gross_margin", "Gross margin", "pct"),
    ("operating_margin", "Operating margin", "pct"),
    ("net_margin", "Net margin", "pct"),
    ("roe", "ROE", "pct"),
    ("roa", "ROA", "pct"),
    ("roce", "ROCE", "pct"),
    ("debt_to_equity", "Debt / equity", "num"),
    ("current_ratio", "Current ratio", "num"),
    ("asset_turnover", "Asset turnover", "num"),
]


def _pct_change(curr, prev):
    """Growth, or None when it would be meaningless.

    A sign change makes percentage growth nonsense -- going from -$1M to +$2M
    is not '300% growth', it's a turnaround. Returning None keeps the cell
    honest rather than printing a number nobody can interpret.
    """
    if curr is None or prev is None or prev == 0:
        return None
    if prev < 0:
        return None
    return (curr / prev - 1.0) * 100.0


def _series(conn, cik, metric, period_type):
    rows = conn.execute(
        "SELECT period_end, val FROM fundamentals "
        "WHERE cik=? AND metric=? AND period_type=? ORDER BY period_end DESC",
        (cik, metric, period_type),
    ).fetchall()
    return {r["period_end"]: r["val"] for r in rows}


def periods(conn, cik, period_type, limit=10):
    rows = conn.execute(
        "SELECT DISTINCT period_end FROM fundamentals "
        "WHERE cik=? AND period_type=? ORDER BY period_end DESC LIMIT ?",
        (cik, period_type, limit),
    ).fetchall()
    return [r[0] for r in rows][::-1]          # oldest -> newest for display


def _nearest_instant(conn, cik, on_or_before):
    """Balance-sheet value closest to a flow period's end date.

    Instants and durations don't share period keys, so a naive join drops the
    balance sheet entirely. Matching on date is what lets ROE and D/E appear
    in a table whose columns are fiscal years.
    """
    return conn.execute(
        "SELECT metric, val FROM fundamentals WHERE cik=? AND period_type='INSTANT' "
        "AND period_end <= ? AND period_end >= date(?, '-120 days')",
        (cik, on_or_before, on_or_before),
    ).fetchall()


def instants_at(conn, cik, period_end):
    out = {}
    for r in _nearest_instant(conn, cik, period_end):
        out.setdefault(r["metric"], r["val"])
    return out


def statement(conn, cik, period_type="FY", limit=10):
    """Metrics x periods, with YoY (and QoQ for quarters)."""
    cols = periods(conn, cik, period_type, limit)
    if not cols:
        return {"periods": [], "rows": []}

    series = {m: _series(conn, cik, m, period_type) for m, _, _ in FLOW_ROWS}
    step = 4 if period_type == "Q" else 1        # same quarter last year

    rows = []
    for metric, label, kind in FLOW_ROWS:
        vals = [series[metric].get(p) for p in cols]
        if all(v is None for v in vals):
            continue

        yoy, qoq = [], []
        for i, p in enumerate(cols):
            yoy.append(_pct_change(vals[i], vals[i - step]) if i >= step else None)
            qoq.append(_pct_change(vals[i], vals[i - 1]) if i >= 1 else None)

        rows.append({"metric": metric, "label": label, "kind": kind,
                     "vals": vals, "yoy": yoy, "qoq": qoq})

    return {"periods": cols, "rows": rows, "period_type": period_type}


def balance_sheet(conn, cik, limit=10):
    """Instants, one column per year-end."""
    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_end FROM fundamentals "
        "WHERE cik=? AND period_type='INSTANT' ORDER BY period_end DESC", (cik,))]
    # One per calendar year, the latest in each.
    by_year, cols = {}, []
    for d in all_dates:
        by_year.setdefault(d[:4], d)
    cols = [by_year[y] for y in sorted(by_year)][-limit:]
    if not cols:
        return {"periods": [], "rows": []}

    series = {m: _series(conn, cik, m, "INSTANT") for m, _, _ in STOCK_ROWS}
    rows = []
    for metric, label, kind in STOCK_ROWS:
        vals = [series[metric].get(p) for p in cols]
        if all(v is None for v in vals):
            continue
        yoy = [_pct_change(vals[i], vals[i - 1]) if i else None
               for i in range(len(cols))]
        rows.append({"metric": metric, "label": label, "kind": kind,
                     "vals": vals, "yoy": yoy, "qoq": [None] * len(cols)})
    return {"periods": cols, "rows": rows, "period_type": "INSTANT"}


def ratios(conn, cik, limit=10):
    """Ratio history: one column per fiscal year.

    Computed per period from that period's own inputs, not from today's
    snapshot -- otherwise every year would show the same number.
    """
    cols = periods(conn, cik, "FY", limit)
    if not cols:
        return {"periods": [], "rows": []}

    flows = {m: _series(conn, cik, m, "FY")
             for m in ("revenue", "gross_profit", "cost_of_revenue",
                       "operating_income", "net_income")}

    computed = {name: [] for name, _, _ in RATIO_ROWS}
    for p in cols:
        inst = instants_at(conn, cik, p)
        rev = flows["revenue"].get(p)
        gp = flows["gross_profit"].get(p)
        cogs = flows["cost_of_revenue"].get(p)
        if gp is None and rev is not None and cogs is not None:
            gp = rev - cogs
        oi = flows["operating_income"].get(p)
        ni = flows["net_income"].get(p)

        assets = inst.get("total_assets")
        equity = inst.get("total_equity")
        cur_l = inst.get("current_liabilities")
        cur_a = inst.get("current_assets")
        debt = (inst.get("short_term_debt") or 0) + (inst.get("long_term_debt") or 0) \
            if (inst.get("short_term_debt") is not None
                or inst.get("long_term_debt") is not None) else None

        def div(a, b, scale=1.0):
            if a is None or not b or b <= 0:
                return None
            return a / b * scale

        # Capital employed = total assets less current liabilities. ROCE is the
        # return on what the business actually has tied up, which is why it's
        # the ratio operators use where ROE flatters leveraged balance sheets.
        capital_employed = None
        if assets is not None and cur_l is not None:
            capital_employed = assets - cur_l

        vals = {
            "gross_margin": div(gp, rev, 100),
            "operating_margin": div(oi, rev, 100),
            "net_margin": div(ni, rev, 100),
            "roe": div(ni, equity, 100),
            "roa": div(ni, assets, 100),
            "roce": div(oi, capital_employed, 100),
            "debt_to_equity": div(debt, equity),
            "current_ratio": div(cur_a, cur_l),
            "asset_turnover": div(rev, assets),
        }
        for k, v in vals.items():
            computed[k].append(v)

    rows = []
    for name, label, kind in RATIO_ROWS:
        vals = computed[name]
        if all(v is None for v in vals):
            continue
        rows.append({"metric": name, "label": label, "kind": kind,
                     "vals": vals,
                     "yoy": [None] * len(cols), "qoq": [None] * len(cols)})
    return {"periods": cols, "rows": rows, "period_type": "FY"}


def peers(conn, cik, limit=8):
    """Same industry, closest by size.

    Industry comes from the SIC description on the filing. Crude compared to
    a real classification, but it's free, it's on every filer, and 'nearest
    market cap in the same SIC' is a defensible peer set.
    """
    me = conn.execute(
        "SELECT ticker, sic_description, market_cap FROM snapshot WHERE cik=?",
        (cik,)).fetchone()
    if not me or not me["sic_description"]:
        return []
    return conn.execute(
        "SELECT ticker, name, market_cap, pe, pb, roe, net_margin, revenue_cagr_3y "
        "FROM snapshot WHERE sic_description = ? AND cik != ? AND ticker IS NOT NULL "
        "ORDER BY ABS(COALESCE(market_cap,0) - ?) LIMIT ?",
        (me["sic_description"], cik, me["market_cap"] or 0, limit),
    ).fetchall()
