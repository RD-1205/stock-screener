"""
facts -> fundamentals -> snapshot.

Three problems get solved here, and they are the three that make or break a
screener. None of them are visible until you look at real filings.

PROBLEM 1: restatements.
   The same quarter is reported many times (original 10-Q, restated in the
   10-K, again as a comparative next year). We keep every version in `facts`
   and resolve to the most recently *filed* one here.

PROBLEM 2: nobody files a Q4.
   US companies file three 10-Qs and one 10-K. There is no Q4 income
   statement anywhere in EDGAR. If you naively "sum the last 4 quarters" for
   TTM you will get three quarters and a hole, and every TTM number on your
   site will be ~25% too low. Q4 must be derived: FY minus the 9-month YTD.

PROBLEM 3: YTD vs discrete quarters.
   10-Qs report cash flow year-to-date, not for the quarter. Q2's cash flow
   figure covers 6 months. Differencing consecutive YTD periods that share a
   fiscal-year start is the only correct way to get a discrete quarter.
"""

from datetime import date

from .concepts import METRICS, METRICS_BY_NAME, DURATION, INSTANT, resolve
from . import splits as splits_mod

# Per-share metrics that restate on a split (see screener/splits.py, P5 in
# docs/PENDING-CHANGES.md). Only applied to the CURRENT view (`fundamentals`)
# -- a point-in-time vintage must show exactly what was on file at that
# as_of date, mixed basis and all, or it stops being point-in-time.
SPLIT_ADJUSTED_PER_SHARE = {"eps_basic", "eps_diluted"}
SPLIT_ADJUSTED_SHARE_COUNT = {"shares_diluted", "shares_outstanding"}

# Period classification by duration in days. Filings are not exact -- a
# "quarter" can be 84 or 98 days depending on 52/53-week fiscal calendars.
PERIOD_BANDS = [
    ("Q",    80, 100),
    ("H",   170, 190),
    ("T9",  260, 290),
    ("FY",  340, 400),
]

# What we persist. H and T9 are scaffolding used to derive Q2/Q3/Q4.
KEEP_TYPES = {"Q", "FY", "INSTANT"}


def classify_period(start, end):
    if not start:
        return "INSTANT"
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (ValueError, TypeError):
        return None
    for name, lo, hi in PERIOD_BANDS:
        if lo <= days <= hi:
            return name
    return None


def _load_resolved_facts(conn, cik, as_of=None):
    """Return {(period_start, period_end): {unit: {concept: val}}}, latest filing wins.

    Ordering by `filed` ascending and letting later rows overwrite earlier ones
    is a one-line fix for the restatement problem.

    `as_of` is the entire point-in-time mechanism: filtering to filings that
    existed on that date reconstructs exactly what a screener would have shown
    then. Restatements filed later simply aren't in the result set, so they
    cannot leak backwards into history.
    """
    sql = ("SELECT concept, unit, period_start, period_end, val, filed "
           "FROM facts WHERE cik = ?")
    params = [cik]
    if as_of:
        sql += " AND filed <= ?"
        params.append(as_of)
    sql += " ORDER BY filed ASC, accn ASC"

    rows = conn.execute(sql, params).fetchall()

    periods = {}
    filed_at = {}
    for r in rows:
        key = (r["period_start"], r["period_end"])
        periods.setdefault(key, {}).setdefault(r["unit"], {})[r["concept"]] = r["val"]
        filed_at[key] = r["filed"]
    return periods, filed_at


def _metric_values(by_unit, kind):
    """Resolve every metric of the given kind from one period's raw tags."""
    out = {}
    for m in METRICS:
        if m.kind != kind:
            continue
        val, concept = resolve(m.name, by_unit.get(m.unit, {}))
        if val is not None:
            out[m.name] = (val, concept)
    return out


CHAIN_ORDER = ["Q", "H", "T9", "FY"]


def normalize_company(conn, cik, as_of=None, table="fundamentals"):
    """Build the `fundamentals` rows for one company. Returns rows written."""
    periods, filed_at = _load_resolved_facts(conn, cik, as_of)

    # bucket: fiscal_year_start -> {period_type -> (period_end, metrics, filed)}
    duration_buckets = {}
    out = {}          # (metric, period_end, period_type) -> (val, concept, derived, filed)

    def emit(metric, period_end, ptype, val, concept, derived, filed):
        key = (metric, period_end, ptype)
        prev = out.get(key)
        # A directly-reported figure always beats a derived one.
        if prev is not None and prev[2] == 0 and derived == 1:
            return
        out[key] = (val, concept, derived, filed)

    for (pstart, pend), by_unit in periods.items():
        ptype = classify_period(pstart, pend)
        if ptype is None:
            continue
        filed = filed_at[(pstart, pend)]

        if ptype == "INSTANT":
            for name, (val, concept) in _metric_values(by_unit, INSTANT).items():
                emit(name, pend, "INSTANT", val, concept, 0, filed)
            continue

        vals = _metric_values(by_unit, DURATION)
        if vals:
            duration_buckets.setdefault(pstart, {})[ptype] = (pend, vals, filed)

    fy_windows = []

    for fy_start, buckets in duration_buckets.items():
        # Straight passthrough for anything actually reported as a quarter or year.
        for ptype in ("Q", "FY"):
            if ptype in buckets:
                pend, vals, filed = buckets[ptype]
                for name, (val, concept) in vals.items():
                    emit(name, pend, ptype, val, concept, 0, filed)
        if "FY" in buckets:
            fy_windows.append((fy_start, buckets["FY"][0], buckets["FY"][2]))

        # --- STRATEGY 1: difference the YTD chain -----------------------
        # Q1 -> H(6mo) -> T9(9mo) -> FY(12mo), all sharing fy_start.
        # Only difference ADJACENT links. Differencing across a gap (e.g.
        # FY minus Q1 when the 6mo and 9mo columns are absent) produces a
        # 9-month number mislabelled as a quarter -- silent, and poisons TTM.
        present = [(lbl, buckets[lbl]) for lbl in CHAIN_ORDER if lbl in buckets]
        for (lbl_prev, prev), (lbl_cur, cur) in zip(present, present[1:]):
            if CHAIN_ORDER.index(lbl_cur) - CHAIN_ORDER.index(lbl_prev) != 1:
                continue
            cur_end, cur_vals, cur_filed = cur
            _, prev_vals, _ = prev
            for name, (val, concept) in cur_vals.items():
                # EPS and share counts are weighted averages -- subtracting
                # them across periods is meaningless. USD flows only.
                if METRICS_BY_NAME[name].unit != "USD":
                    continue
                if name not in prev_vals:
                    continue
                emit(name, cur_end, "Q", val - prev_vals[name][0], concept, 1, cur_filed)

    # --- STRATEGY 2: Q4 = FY - (Q1 + Q2 + Q3) ---------------------------
    # The common case. Companies file three 10-Qs with discrete quarterly
    # income statements and no YTD column we can use, then a 10-K. The
    # fourth quarter exists nowhere in EDGAR and must be backed out.
    for fy_start, fy_end, fy_filed in fy_windows:
        for (metric, pend, ptype), (val, concept, _d, _f) in list(out.items()):
            if ptype != "FY" or pend != fy_end:
                continue
            if METRICS_BY_NAME[metric].unit != "USD":
                continue
            if (metric, fy_end, "Q") in out:
                continue                     # Q4 already reported or derived
            qs = [
                v[0] for (m, pe, pt), v in out.items()
                if m == metric and pt == "Q" and fy_start <= pe <= fy_end
            ]
            if len(qs) == 3:
                emit(metric, fy_end, "Q", val - sum(qs), concept, 1, fy_filed)

    # Split adjustment belongs to the current view only -- see module docstring
    # at SPLIT_ADJUSTED_PER_SHARE above.
    split_events = splits_mod.load_splits(conn, cik) if table == "fundamentals" else []

    rows = []
    for (metric, pend, ptype), (val, concept, derived, filed) in out.items():
        if split_events and val is not None:
            if metric in SPLIT_ADJUSTED_PER_SHARE:
                val = val / splits_mod.factor_after(split_events, filed)
            elif metric in SPLIT_ADJUSTED_SHARE_COUNT:
                val = val * splits_mod.factor_after(split_events, filed)
        rows.append((cik, metric, pend, ptype, val, concept, derived, filed))
    if rows:
        # table is chosen by us, never by user input -- guard anyway.
        assert table in ("fundamentals", "fundamentals_pit"), table
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} "
            "(cik, metric, period_end, period_type, val, source_concept, derived, filed) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def normalize_all(conn, ciks=None, as_of=None, table="fundamentals"):
    if ciks is None:
        sql = "SELECT DISTINCT cik FROM facts"
        params = ()
        if as_of:
            sql += " WHERE filed <= ?"
            params = (as_of,)
        ciks = [r[0] for r in conn.execute(sql, params)]
    total = 0
    for cik in ciks:
        total += normalize_company(conn, cik, as_of=as_of, table=table)
    conn.commit()
    return total


# ---------------------------------------------------------------------------
# Snapshot / ratios
# ---------------------------------------------------------------------------

FUND_TABLES = ("fundamentals", "fundamentals_pit")


def _series(conn, cik, metric, period_type, table="fundamentals"):
    assert table in FUND_TABLES, table
    return conn.execute(
        f"SELECT period_end, val FROM {table} "
        "WHERE cik=? AND metric=? AND period_type=? ORDER BY period_end DESC",
        (cik, metric, period_type),
    ).fetchall()


TTM_MIN_DAYS = 330
TTM_MAX_DAYS = 400


def ttm(conn, cik, metric, table="fundamentals"):
    """Sum the last four discrete quarters -- but only if they really span a year.

    The contiguity check is not paranoia. If Q4 is missing, the four most
    recent quarterly rows are Q3/Q2/Q1 of this year plus Q4 of LAST year:
    twelve months of calendar span but a duplicated Q4 and a missing one, or
    a 9-month gap. Summing them blindly gave a number ~7% off in testing --
    small enough that nobody notices, large enough to make every margin and
    P/E on the site quietly wrong. Verify the span, then fall back to the
    last reported fiscal year, which is always internally consistent.
    """
    qs = _series(conn, cik, metric, "Q", table)
    if len(qs) >= 4:
        window = qs[:4]
        try:
            newest = date.fromisoformat(window[0]["period_end"])
            oldest = date.fromisoformat(window[3]["period_end"])
            span = (newest - oldest).days
            ends = {r["period_end"] for r in window}
            if TTM_MIN_DAYS - 92 <= span <= TTM_MAX_DAYS - 92 and len(ends) == 4:
                return sum(r["val"] for r in window)
        except (ValueError, TypeError):
            pass
    fys = _series(conn, cik, metric, "FY", table)
    if fys:
        return fys[0]["val"]
    return None


def latest_instant(conn, cik, metric, table="fundamentals"):
    rows = _series(conn, cik, metric, "INSTANT", table)
    return rows[0]["val"] if rows else None


def cagr(conn, cik, metric, years=3, table="fundamentals"):
    rows = _series(conn, cik, metric, "FY", table)
    if len(rows) <= years:
        return None
    new, old = rows[0]["val"], rows[years]["val"]
    if old is None or new is None or old <= 0 or new <= 0:
        return None   # CAGR is undefined across a sign change; don't fake it
    return ((new / old) ** (1.0 / years) - 1.0) * 100.0


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def pct(x):
    return None if x is None else x * 100.0


def price_asof(conn, ticker, as_of=None):
    """Last close on or before `as_of`. None means 'most recent we have'."""
    if not ticker:
        return None
    if as_of:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date <= ? "
            "ORDER BY date DESC LIMIT 1", (ticker, as_of),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return row["close"] if row else None


def compute_metrics(conn, cik, price, table="fundamentals"):
    """All ratios for one company from one fundamentals table. Returns a dict.

    Split out from the insert so the live snapshot and every historical
    vintage run through byte-identical arithmetic. If these ever diverge,
    your backtest is measuring your own code changes rather than the market.
    """
    T = lambda m: ttm(conn, cik, m, table)              # noqa: E731
    I = lambda m: latest_instant(conn, cik, m, table)   # noqa: E731

    revenue, op_inc, net_inc = T("revenue"), T("operating_income"), T("net_income")
    eps, ocf, capex = T("eps_diluted"), T("operating_cash_flow"), T("capex")
    shares_d = T("shares_diluted")

    gross = T("gross_profit")
    cogs = T("cost_of_revenue")
    if gross is None and revenue is not None and cogs is not None:
        gross = revenue - cogs      # often untagged; back it out when we can

    assets, equity, cash = I("total_assets"), I("total_equity"), I("cash")
    cur_a, cur_l = I("current_assets"), I("current_liabilities")
    std, ltd = I("short_term_debt"), I("long_term_debt")
    shares_o = I("shares_outstanding") or shares_d

    total_debt = None
    if std is not None or ltd is not None:
        total_debt = (std or 0.0) + (ltd or 0.0)

    # capex is reported as a positive outflow; FCF subtracts it
    fcf = None if ocf is None or capex is None else ocf - abs(capex)
    mcap = None if price is None or shares_o is None else price * shares_o
    ev = None if mcap is None else mcap + (total_debt or 0.0) - (cash or 0.0)

    return {
        "price": price, "market_cap": mcap, "shares_diluted": shares_d,
        "revenue_ttm": revenue, "gross_profit_ttm": gross,
        "operating_income_ttm": op_inc, "net_income_ttm": net_inc,
        "eps_ttm": eps, "ocf_ttm": ocf, "capex_ttm": capex, "fcf_ttm": fcf,
        "total_assets": assets, "total_equity": equity,
        "total_debt": total_debt, "cash": cash,
        # Guards below are not pedantry: negative equity makes ROE and P/B
        # meaningless (a bankrupt company shows a beautiful ROE), and negative
        # EPS makes P/E nonsense. Null is the honest answer -- showing -3.2x
        # invites users to sort by it and "find" garbage.
        "pe": _div(price, eps) if (eps or 0) > 0 else None,
        "pb": _div(mcap, equity) if (equity or 0) > 0 else None,
        "ps": _div(mcap, revenue),
        "ev": ev,
        "ev_to_ebit": _div(ev, op_inc) if (op_inc or 0) > 0 else None,
        "roe": pct(_div(net_inc, equity)) if (equity or 0) > 0 else None,
        "roa": pct(_div(net_inc, assets)),
        "gross_margin": pct(_div(gross, revenue)),
        "operating_margin": pct(_div(op_inc, revenue)),
        "net_margin": pct(_div(net_inc, revenue)),
        "debt_to_equity": _div(total_debt, equity) if (equity or 0) > 0 else None,
        "current_ratio": _div(cur_a, cur_l),
        "revenue_cagr_3y": cagr(conn, cik, "revenue", table=table),
        "eps_cagr_3y": cagr(conn, cik, "eps_diluted", table=table),
    }


SNAPSHOT_COLS = [
    "price", "market_cap", "shares_diluted",
    "revenue_ttm", "gross_profit_ttm", "operating_income_ttm", "net_income_ttm",
    "eps_ttm", "ocf_ttm", "capex_ttm", "fcf_ttm",
    "total_assets", "total_equity", "total_debt", "cash",
    "pe", "pb", "ps", "ev", "ev_to_ebit",
    "roe", "roa", "gross_margin", "operating_margin", "net_margin",
    "debt_to_equity", "current_ratio", "revenue_cagr_3y", "eps_cagr_3y",
]


def build_snapshot(conn, as_of=None):
    """Current view of every company. Uses all data we have."""
    as_of = as_of or date.today().isoformat()
    ciks = [r[0] for r in conn.execute("SELECT DISTINCT cik FROM fundamentals")]
    written = 0

    cols = ["cik", "ticker", "name", "sic_description", "as_of"] + SNAPSHOT_COLS
    sql = (f"INSERT OR REPLACE INTO snapshot ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")

    for cik in ciks:
        co = conn.execute(
            "SELECT ticker, name, sic_description FROM companies WHERE cik=?", (cik,)
        ).fetchone()
        ticker = co["ticker"] if co else None
        m = compute_metrics(conn, cik, price_asof(conn, ticker), "fundamentals")
        conn.execute(sql, [
            cik, ticker,
            co["name"] if co else None,
            co["sic_description"] if co else None,
            as_of,
        ] + [m[c] for c in SNAPSHOT_COLS])
        written += 1

    conn.commit()
    return written


# ---------------------------------------------------------------------------
# Point-in-time
# ---------------------------------------------------------------------------

def build_snapshot_asof(conn, as_of):
    """Reconstruct the whole market as it was visible on `as_of`.

    Three separate cutoffs have to agree, and getting any one wrong
    reintroduces exactly the lookahead bias this feature exists to remove:

      1. facts    -- only filings with filed <= as_of
      2. prices   -- last close on or before as_of, not today's price
      3. universe -- only companies that had actually filed something by then,
                     which excludes companies that IPO'd later. Screening a
                     2015 vintage against today's company list is survivorship
                     bias wearing a different hat.
    """
    conn.execute("DELETE FROM fundamentals_pit")
    ciks = [r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE filed <= ?", (as_of,)
    )]
    normalize_all(conn, ciks=ciks, as_of=as_of, table="fundamentals_pit")

    cols = (["as_of", "cik", "ticker", "name", "sic_description"]
            + SNAPSHOT_COLS + ["data_age_days"])
    sql = (f"INSERT OR REPLACE INTO snapshot_history ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")

    written = 0
    for cik in ciks:
        co = conn.execute(
            "SELECT ticker, name, sic_description FROM companies WHERE cik=?", (cik,)
        ).fetchone()
        ticker = co["ticker"] if co else None
        px = price_asof(conn, ticker, as_of)
        m = compute_metrics(conn, cik, px, "fundamentals_pit")

        newest = conn.execute(
            "SELECT MAX(filed) AS f FROM facts WHERE cik=? AND filed <= ?",
            (cik, as_of),
        ).fetchone()["f"]
        age = None
        if newest:
            try:
                age = (date.fromisoformat(as_of) - date.fromisoformat(newest)).days
            except (ValueError, TypeError):
                age = None

        conn.execute(sql, [
            as_of, cik, ticker,
            co["name"] if co else None,
            co["sic_description"] if co else None,
        ] + [m[c] for c in SNAPSHOT_COLS] + [age])
        written += 1

    conn.execute("DELETE FROM fundamentals_pit")
    conn.commit()
    return written


def month_ends(start, end):
    """Quarter-end vintages between two ISO dates -- a sane default cadence.

    Daily vintages would be 250x the storage for no extra insight; fundamentals
    only change when someone files.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    for year in range(s.year, e.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            d = date(year, month, day)
            if s <= d <= e:
                out.append(d.isoformat())
    return out


def build_history(conn, dates, progress=None):
    total = 0
    for d in dates:
        n = build_snapshot_asof(conn, d)
        total += n
        if progress:
            progress(f"  {d}: {n} companies")
    return total
