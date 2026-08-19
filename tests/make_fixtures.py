"""
Generate synthetic companyfacts JSON that reproduces the real EDGAR quirks.

Testing against live SEC data is slow, non-deterministic, and rate-limited.
These fixtures bake in the things that actually break naive pipelines:

  1. Four companies, four DIFFERENT revenue tags        (normalization)
  2. A restated quarter filed a year later              (dedup by `filed`,
                                                         and point-in-time)
  3. No Q4 filing anywhere                              (Q4 derivation)
  4. Cash flow reported year-to-date only               (YTD differencing)
  5. A bank: no gross profit, no cost of revenue        (metric gaps)
  6. Negative shareholders' equity                      (ROE/PB must be NULL)
  7. A September fiscal year end                        (fiscal != calendar)
  8. Filing lag -- data is filed weeks AFTER period end (point-in-time)

Item 8 is the one people forget. A 10-K for the year ending 2023-12-31 is
filed in late February 2024. Anyone screening on 2024-01-15 could not see it.
If your point-in-time engine keys off period_end instead of filed, it leaks
the future and every backtest looks great.
"""

import calendar
import json
import os
from datetime import date, timedelta


# ---------------------------------------------------------------- helpers

def _fact(start, end, val, accn, fy, fp, form, filed):
    d = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
         "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def _eom(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def _fy_quarters(fy_end_year, fy_end_month):
    """Quarter (start, end) pairs for a fiscal year ending in a given month.

    fy_end_month=12 gives calendar quarters. fy_end_month=9 gives Apple's
    calendar: FY2024 runs Oct 2023 -> Sep 2024.
    """
    q_ends = []
    for i in range(1, 5):
        m, y = fy_end_month - 3 * (4 - i), fy_end_year
        while m <= 0:
            m += 12
            y -= 1
        q_ends.append(_eom(y, m))

    fy_start = _eom(fy_end_year - 1, fy_end_month) + timedelta(days=1)
    starts = [fy_start] + [qe + timedelta(days=1) for qe in q_ends[:-1]]
    quarters = [(s.isoformat(), e.isoformat()) for s, e in zip(starts, q_ends)]
    return quarters, fy_start.isoformat(), q_ends[-1].isoformat()


def _blank(tags):
    out = {}
    for tag, unit in tags:
        out[tag] = {"label": tag, "units": {unit: []}}
    return out


BASE_TAGS = [
    ("NetIncomeLoss", "USD"),
    ("OperatingIncomeLoss", "USD"),
    ("NetCashProvidedByUsedInOperatingActivities", "USD"),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
    ("Assets", "USD"),
    ("Liabilities", "USD"),
    ("StockholdersEquity", "USD"),
    ("CashAndCashEquivalentsAtCarryingValue", "USD"),
    ("AssetsCurrent", "USD"),
    ("LiabilitiesCurrent", "USD"),
    ("LongTermDebtNoncurrent", "USD"),
    ("EarningsPerShareDiluted", "USD/shares"),
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
    ("CommonStockSharesOutstanding", "shares"),
]


# ---------------------------------------------------------------- builder

def build_company(cik, name, revenue_tag, base_revenue, margin, years,
                  growth=0.10, fy_end_month=12, has_cogs=True,
                  equity_multiple=1.1, shares=1_000_000_000.0,
                  q_lag_days=35, k_lag_days=52):
    """One filer with N years of history, filed the way real companies file.

    q_lag_days / k_lag_days are the gap between period end and filing date.
    Real 10-Qs land ~40 days out, 10-Ks ~60. This is what makes point-in-time
    testing meaningful.
    """
    tags = list(BASE_TAGS) + [(revenue_tag, "USD")]
    if has_cogs:
        tags.append(("CostOfRevenue", "USD"))
    facts = _blank(tags)

    weights = [0.22, 0.24, 0.25, 0.29]      # real seasonality, uneven

    for yi, fy in enumerate(years):
        annual_rev = base_revenue * ((1 + growth) ** yi)
        quarters, fy_start, fy_end = _fy_quarters(fy, fy_end_month)
        k_filed = (date.fromisoformat(fy_end) + timedelta(days=k_lag_days)).isoformat()
        accn_k = f"{cik:010d}-{str(fy)[-2:]}-000001"

        ytd_rev = ytd_ocf = 0.0
        for qi, (qs, qe) in enumerate(quarters):
            rev = annual_rev * weights[qi]
            ni = rev * margin
            oi = rev * margin * 1.3
            ocf = ni * 1.2
            ytd_rev += rev
            ytd_ocf += ocf

            accn_q = f"{cik:010d}-{str(fy)[-2:]}-00000{qi + 2}"
            q_filed = (date.fromisoformat(qe) + timedelta(days=q_lag_days)).isoformat()
            fp = f"Q{qi + 1}"

            if qi < 3:
                # --- 10-Q: discrete quarter on the income statement ...
                add = [
                    (revenue_tag, "USD", rev),
                    ("NetIncomeLoss", "USD", ni),
                    ("OperatingIncomeLoss", "USD", oi),
                    ("EarningsPerShareDiluted", "USD/shares", ni / shares),
                    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", shares),
                ]
                if has_cogs:
                    add.append(("CostOfRevenue", "USD", rev * 0.6))
                for tag, unit, val in add:
                    facts[tag]["units"][unit].append(
                        _fact(qs, qe, val, accn_q, fy, fp, "10-Q", q_filed))

                # ... but cash flow is YEAR TO DATE, as in every real 10-Q
                facts["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"].append(
                    _fact(fy_start, qe, ytd_ocf, accn_q, fy, fp, "10-Q", q_filed))
                # ... and the YTD income statement column too (6mo / 9mo)
                if qi > 0:
                    facts[revenue_tag]["units"]["USD"].append(
                        _fact(fy_start, qe, ytd_rev, accn_q, fy, fp, "10-Q", q_filed))

            # balance sheet: instant fact at every quarter end
            for tag, val in (
                ("Assets", annual_rev * 1.8 + qi * 1e6),
                ("Liabilities", annual_rev * 0.7),
                ("StockholdersEquity", annual_rev * equity_multiple),
                ("CashAndCashEquivalentsAtCarryingValue", annual_rev * 0.25),
                ("AssetsCurrent", annual_rev * 0.9),
                ("LiabilitiesCurrent", annual_rev * 0.45),
                ("LongTermDebtNoncurrent", annual_rev * 0.30),
            ):
                facts[tag]["units"]["USD"].append(
                    _fact(None, qe, val, accn_q, fy, fp, "10-Q", q_filed))
            facts["CommonStockSharesOutstanding"]["units"]["shares"].append(
                _fact(None, qe, shares, accn_q, fy, fp, "10-Q", q_filed))

        # --- 10-K: full year only. NOTE: no Q4 income statement exists. ---
        annual = [
            (revenue_tag, "USD", annual_rev),
            ("NetIncomeLoss", "USD", annual_rev * margin),
            ("OperatingIncomeLoss", "USD", annual_rev * margin * 1.3),
            ("EarningsPerShareDiluted", "USD/shares", annual_rev * margin / shares),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", shares),
            ("NetCashProvidedByUsedInOperatingActivities", "USD",
             annual_rev * margin * 1.2),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", annual_rev * 0.06),
        ]
        if has_cogs:
            annual.append(("CostOfRevenue", "USD", annual_rev * 0.6))
        for tag, unit, val in annual:
            facts[tag]["units"][unit].append(
                _fact(fy_start, fy_end, val, accn_k, fy, "FY", "10-K", k_filed))

    return {"cik": cik, "entityName": name, "facts": {"us-gaap": facts}}


def add_restatement(doc, revenue_tag, period=("2022-01-01", "2022-03-31"),
                    factor=1.5, filed="2023-06-01"):
    """Refile a past quarter with a corrected number, a year later.

    Two things must hold afterwards:
      - today's view shows the RESTATED figure
      - a point-in-time view dated before `filed` shows the ORIGINAL
    A pipeline that overwrites facts on ingest can only ever satisfy one.
    """
    entries = doc["facts"]["us-gaap"][revenue_tag]["units"]["USD"]
    start, end = period
    for e in list(entries):
        if e.get("start") == start and e["end"] == end:
            restated = dict(e)
            restated["val"] = e["val"] * factor
            restated["accn"] = "9999999999-23-000009"
            restated["filed"] = filed
            restated["form"] = "10-K/A"
            entries.append(restated)
            return {"original": e["val"], "restated": restated["val"],
                    "filed": filed, "period_end": end}
    return None


# ---------------------------------------------------------------- prices

def price_series(ticker, start, end, start_px, annual_drift=0.10, step_days=7):
    """Deterministic weekly closes. No randomness -- tests must be reproducible.

    The wobble is phase-shifted per ticker. Without that every symbol shares
    the same last-vs-previous ratio and the ticker belt renders the identical
    percentage change for the whole market, which looks broken. Derived from
    the ticker name so it stays reproducible.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    phase = sum(ord(ch) for ch in ticker) % 13
    base_vol = 2e5 * (1 + sum(ord(ch) for ch in ticker) % 40)
    amplitude = 0.04 + (sum(ord(ch) for ch in ticker) % 7) * 0.012
    rows, d, i = [], s, 0
    while d <= e:
        # smooth compounding plus a per-ticker seasonal wobble
        t = (d - s).days / 365.0
        wobble = 1.0 + amplitude * (((i + phase) % 13) - 6) / 6.0
        px = start_px * ((1 + annual_drift) ** t) * wobble
        # Volume varies per ticker and drifts week to week. A constant would
        # make "most active" sort arbitrarily, which reads as broken.
        vol = base_vol * (1.0 + 0.5 * (((i + phase) % 7) - 3) / 3.0)
        rows.append((ticker, d.isoformat(), px, px * 1.01, px * 0.99, px, round(vol)))
        d += timedelta(days=step_days)
        i += 1
    return rows


# ---------------------------------------------------------------- specs

YEARS = [2021, 2022, 2023, 2024]

SPECS = [
    # cik, ticker, name, revenue tag, base rev, margin, growth, kwargs
    (100001, "MODT", "Modern Tagger Inc",
     "RevenueFromContractWithCustomerExcludingAssessedTax",
     10e9, 0.22, 0.15, {}),

    (100002, "LEGC", "Legacy Tagger Corp",
     "Revenues", 4e9, 0.08, 0.03, {}),

    (100003, "DEPR", "Deprecated Tagger Co",
     "SalesRevenueNet", 800e6, 0.14, 0.25, {}),

    # A bank: no cost of revenue, so gross margin is genuinely undefined
    # rather than zero. Screeners that show 0% here are lying.
    (100004, "BANQ", "Fixture National Bank",
     "Revenues", 6e9, 0.28, 0.05, {"has_cogs": False}),

    # Negative equity -- ROE, P/B and debt/equity must all come back NULL.
    # A naive pipeline reports a spectacular ROE for a company that is
    # technically insolvent, and it sorts straight to the top of any
    # "highest ROE" screen.
    (100005, "NEGE", "Leveraged Buyout Co",
     "Revenues", 2e9, 0.05, 0.02, {"equity_multiple": -0.35}),

    # September fiscal year end, like Apple. "FY2024" here covers a
    # different 12 months than everyone else's FY2024.
    (100006, "SEPT", "September Yearend Inc",
     "RevenueFromContractWithCustomerExcludingAssessedTax",
     3e9, 0.18, 0.12, {"fy_end_month": 9}),
]

# SIC codes chosen to land in different browse sectors.
SIC_BY_CIK = {
    100001: ("7372", "Prepackaged software"),
    100002: ("3711", "Motor vehicles"),
    100003: ("3674", "Semiconductors"),
    100004: ("6022", "State commercial banks"),
    100005: ("5812", "Eating places"),
    100006: ("3571", "Electronic computers"),
}

INDEX_FIXTURES = [
    ("sp500", "S&P 500", ["MODT", "LEGC", "BANQ", "SEPT"]),
    ("nasdaq100", "Nasdaq 100", ["MODT", "DEPR", "SEPT"]),
    ("dow30", "Dow 30", ["MODT", "BANQ"]),
]

PRICES = {"MODT": 52.0, "LEGC": 4.6, "DEPR": 9.0,
          "BANQ": 31.0, "NEGE": 7.0, "SEPT": 24.0}


def write_all(outdir):
    os.makedirs(outdir, exist_ok=True)
    meta = {}
    for cik, ticker, name, tag, rev, margin, growth, kw in SPECS:
        doc = build_company(cik, name, tag, rev, margin, YEARS, growth, **kw)
        info = {"ticker": ticker, "name": name, "tag": tag}
        if cik == 100001:
            info["restatement"] = add_restatement(doc, tag)
        meta[cik] = info
        with open(os.path.join(outdir, f"CIK{cik:010d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(doc, f)
    return meta


def seed_db(conn, fixtures_dir):
    """Load fixtures + tickers + a price history. Used by tests and the demo."""
    from screener import ingest, transform

    ingest.ingest_dir(conn, fixtures_dir)
    for cik, ticker, name, *_ in SPECS:
        sic, sic_desc = SIC_BY_CIK.get(cik, ("7372", "Prepackaged software"))
        conn.execute(
            "UPDATE companies SET ticker=?, sic=?, sic_description=? WHERE cik=?",
            (ticker, sic, sic_desc, cik))
        conn.execute("UPDATE snapshot SET sic_description=? WHERE cik=?",
                     (sic_desc, cik))
    for slug, name, members in INDEX_FIXTURES:
        for t in members:
            conn.execute("INSERT OR REPLACE INTO index_members VALUES (?,?,?,?)",
                         (slug, name, t, "2026-01-01"))
    for ticker, px in PRICES.items():
        rows = price_series(ticker, "2021-01-01", "2025-06-30", px)
        conn.executemany(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    transform.normalize_all(conn)
    transform.build_snapshot(conn)
    return meta_summary(conn)


def meta_summary(conn):
    return {
        "companies": conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
        "facts": conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
        "fundamentals": conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0],
        "prices": conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    print(json.dumps(write_all(target), indent=2, default=str))
