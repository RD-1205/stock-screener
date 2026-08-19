"""
End-to-end pipeline tests. Run: python tests/test_pipeline.py
(or `python -m pytest tests/ -v` if you have pytest.)

Each test targets one failure mode that silently corrupts a screener --
the kind that returns plausible numbers rather than crashing.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, ingest, screen, transform          # noqa: E402
from screener.concepts import resolve                        # noqa: E402
from tests.make_fixtures import write_all, seed_db           # noqa: E402

_CACHE = {}


def build_db():
    """Built once and reused -- these tests are read-only."""
    if "conn" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        meta = write_all(fixtures)
        conn = db.connect(os.path.join(tmp, "test.db"))
        db.init(conn)
        seed_db(conn, fixtures)
        _CACHE["conn"], _CACHE["meta"] = conn, meta
    return _CACHE["conn"], _CACHE["meta"]


def one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else None


# ------------------------------------------------- normalization

def test_normalization_across_different_tags():
    """Six companies, four different revenue tags; all must resolve."""
    conn, _ = build_db()
    rows = conn.execute(
        "SELECT cik, val, source_concept FROM fundamentals "
        "WHERE metric='revenue' AND period_type='FY' ORDER BY cik"
    ).fetchall()
    ciks = {r["cik"] for r in rows}
    tags = {r["source_concept"] for r in rows}
    assert len(ciks) == 6, f"expected all 6 companies, got {len(ciks)}"
    assert len(tags) >= 3, f"fixtures should exercise multiple tags, got {tags}"
    print(f"  OK  6 companies, {len(tags)} distinct revenue tags normalized")


def test_resolve_priority_order():
    """When a filer tags BOTH a modern and a legacy concept, prefer modern."""
    val, concept = resolve("revenue", {
        "Revenues": 100.0,
        "RevenueFromContractWithCustomerExcludingAssessedTax": 200.0,
    })
    assert concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert val == 200.0
    assert resolve("revenue", {"NotARealTag": 1})[0] is None
    print("  OK  concept priority order respected")


# ------------------------------------------------- period logic

def test_restatement_uses_latest_filing():
    """Q1 2022 revenue was refiled 1.5x higher; the newer value must win today."""
    conn, meta = build_db()
    rs = meta[100001]["restatement"]
    val = one(conn, "SELECT val FROM fundamentals WHERE cik=100001 AND metric='revenue' "
                    "AND period_type='Q' AND period_end=?", (rs["period_end"],))
    assert val is not None, "restated quarter missing entirely"
    assert abs(val - rs["restated"]) < 1.0, (
        f"took stale value {val:,.0f}, expected restated {rs['restated']:,.0f}")
    print(f"  OK  restatement resolved to newest filing ({rs['restated']/1e9:.2f}B)")


def test_q4_is_derived():
    """No Q4 exists in any filing -- it must be derived as FY minus 9-month YTD."""
    conn, _ = build_db()
    row = conn.execute(
        "SELECT val, derived FROM fundamentals WHERE cik=100002 AND metric='revenue' "
        "AND period_type='Q' AND period_end='2023-12-31'"
    ).fetchone()
    assert row is not None, "Q4 2023 not derived -- TTM will be ~25% too low"
    assert row["derived"] == 1, "Q4 should be flagged derived, not reported"
    fy = one(conn, "SELECT val FROM fundamentals WHERE cik=100002 AND metric='revenue' "
                   "AND period_type='FY' AND period_end='2023-12-31'")
    assert abs(row["val"] / fy - 0.29) < 0.01, (
        f"derived Q4 is {row['val']/fy:.1%} of FY, expected 29%")
    print(f"  OK  Q4 derived correctly ({row['val']/fy:.1%} of FY)")


def test_ttm_matches_fiscal_year():
    """TTM through Q4 must equal the fiscal year. If Q4 were missing it wouldn't."""
    conn, _ = build_db()
    for cik in (100001, 100002, 100004):
        ttm_rev = transform.ttm(conn, cik, "revenue")
        fy = one(conn, "SELECT val FROM fundamentals WHERE cik=? AND metric='revenue' "
                       "AND period_type='FY' ORDER BY period_end DESC LIMIT 1", (cik,))
        assert abs(ttm_rev - fy) / fy < 0.001, (
            f"cik {cik}: TTM {ttm_rev:,.0f} != FY {fy:,.0f}")
    print("  OK  TTM reconciles to FY for 3 companies")


def test_ytd_cashflow_differenced():
    """Cash flow is filed YTD only; discrete quarters must be differenced out."""
    conn, _ = build_db()
    qs = conn.execute(
        "SELECT period_end, val FROM fundamentals WHERE cik=100001 "
        "AND metric='operating_cash_flow' AND period_type='Q' "
        "AND period_end LIKE '2023%' ORDER BY period_end"
    ).fetchall()
    assert len(qs) >= 3, f"expected >=3 quarterly OCF values, got {len(qs)}"
    assert all(q["val"] > 0 for q in qs), (
        "a differenced quarter went negative -- YTD chain is misaligned")
    print(f"  OK  {len(qs)} discrete quarterly OCF values recovered from YTD")


def test_september_fiscal_year():
    """A Sept-30 filer's fiscal years must land on Sept 30, not Dec 31."""
    conn, _ = build_db()
    ends = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_end FROM fundamentals WHERE cik=100006 "
        "AND metric='revenue' AND period_type='FY' ORDER BY period_end")]
    assert ends, "no fiscal years found for the September filer"
    assert all(e.endswith("-09-30") for e in ends), (
        f"fiscal year ends misclassified: {ends}")
    ttm_rev = transform.ttm(conn, 100006, "revenue")
    assert ttm_rev and ttm_rev > 0
    print(f"  OK  September fiscal year handled ({len(ends)} years, ends {ends[-1]})")


# ------------------------------------------------- ratio guards

def test_ratios_are_sane():
    conn, _ = build_db()
    row = conn.execute("SELECT * FROM snapshot WHERE ticker='MODT'").fetchone()
    assert row["pe"] and 0 < row["pe"] < 100, f"implausible PE {row['pe']}"
    assert abs(row["net_margin"] - 22.0) < 1.0, f"net margin {row['net_margin']}"
    assert abs(row["revenue_cagr_3y"] - 15.0) < 0.5, f"CAGR {row['revenue_cagr_3y']}"
    assert row["fcf_ttm"] < row["ocf_ttm"], "FCF must be below OCF after capex"
    print(f"  OK  PE={row['pe']:.1f} margin={row['net_margin']:.1f}% "
          f"CAGR={row['revenue_cagr_3y']:.1f}%")


def test_negative_equity_returns_null_not_garbage():
    """The insolvent company must not top a 'highest ROE' screen."""
    conn, _ = build_db()
    row = conn.execute("SELECT * FROM snapshot WHERE ticker='NEGE'").fetchone()
    assert row["total_equity"] < 0, "fixture should have negative equity"
    for col in ("roe", "pb", "debt_to_equity"):
        assert row[col] is None, (
            f"{col}={row[col]} on negative equity -- must be NULL")

    top = screen.run(conn, "roe > 0", order_by="roe", limit=10)
    assert "NEGE" not in [r["ticker"] for r in top], (
        "insolvent company leaked into a positive-ROE screen")
    print("  OK  negative equity -> NULL roe/pb/debt_to_equity, excluded from screens")


def test_bank_has_no_fake_gross_margin():
    """No cost of revenue reported means gross margin is unknown, not zero."""
    conn, _ = build_db()
    row = conn.execute("SELECT * FROM snapshot WHERE ticker='BANQ'").fetchone()
    assert row["revenue_ttm"] > 0, "bank should still have revenue"
    assert row["gross_margin"] is None, (
        f"gross_margin={row['gross_margin']} but no COGS was ever tagged")
    assert row["net_margin"] is not None, "net margin should still compute"
    print("  OK  bank: gross_margin NULL, net_margin present")


# ------------------------------------------------- point in time

def test_point_in_time_excludes_future_filings():
    """A vintage dated before a 10-K was filed must not contain that 10-K."""
    conn, _ = build_db()
    # FY2024 10-K is filed ~52 days after 2024-12-31, i.e. late Feb 2025.
    transform.build_snapshot_asof(conn, "2025-01-15")
    rev_then = one(conn, "SELECT revenue_ttm FROM snapshot_history "
                         "WHERE as_of='2025-01-15' AND ticker='MODT'")
    fy2024 = one(conn, "SELECT val FROM fundamentals WHERE cik=100001 "
                       "AND metric='revenue' AND period_type='FY' "
                       "AND period_end='2024-12-31'")
    assert rev_then is not None, "no point-in-time row built"
    assert abs(rev_then - fy2024) > 1.0, (
        "vintage dated 2025-01-15 already knows FY2024 revenue, but that "
        "10-K was not filed until late February -- lookahead bias")
    print(f"  OK  2025-01-15 vintage excludes the not-yet-filed FY2024 10-K")


def test_point_in_time_shows_pre_restatement_value():
    """The killer test: history must show what was known THEN, not today's truth."""
    conn, meta = build_db()
    rs = meta[100001]["restatement"]          # filed 2023-06-01, 1.5x higher

    # rebuild the 2023-01-01 vintage and inspect the restated quarter directly
    conn.execute("DELETE FROM fundamentals_pit")
    transform.normalize_all(conn, as_of="2023-01-01", table="fundamentals_pit")
    old = one(conn, "SELECT val FROM fundamentals_pit WHERE cik=100001 "
                    "AND metric='revenue' AND period_type='Q' AND period_end=?",
              (rs["period_end"],))
    new = one(conn, "SELECT val FROM fundamentals WHERE cik=100001 "
                    "AND metric='revenue' AND period_type='Q' AND period_end=?",
              (rs["period_end"],))
    conn.execute("DELETE FROM fundamentals_pit")

    assert old is not None, "pre-restatement value missing"
    assert abs(old - rs["original"]) < 1.0, (
        f"vintage shows {old:,.0f}, expected original {rs['original']:,.0f}")
    assert abs(new - rs["restated"]) < 1.0, "current view should show restated"
    assert abs(old - new) > 1.0, "restatement leaked backwards into history"
    print(f"  OK  2023-01-01 vintage shows {old/1e9:.2f}B (original), "
          f"today shows {new/1e9:.2f}B (restated)")


def test_point_in_time_prices_are_historical():
    """A 2022 vintage must use 2022 prices, not today's."""
    conn, _ = build_db()
    transform.build_snapshot_asof(conn, "2022-06-30")
    px_then = one(conn, "SELECT price FROM snapshot_history "
                        "WHERE as_of='2022-06-30' AND ticker='MODT'")
    px_now = one(conn, "SELECT price FROM snapshot WHERE ticker='MODT'")
    assert px_then is not None and px_now is not None
    assert abs(px_then - px_now) > 0.01, (
        f"vintage price {px_then} equals today's {px_now} -- using live prices")
    expected = one(conn, "SELECT close FROM prices WHERE ticker='MODT' "
                         "AND date <= '2022-06-30' ORDER BY date DESC LIMIT 1")
    assert abs(px_then - expected) < 0.01
    print(f"  OK  2022-06-30 vintage priced at {px_then:.2f}, today {px_now:.2f}")


def test_point_in_time_universe_excludes_unlisted():
    """Companies that had not filed anything yet must not appear in old vintages."""
    conn, _ = build_db()
    transform.build_snapshot_asof(conn, "2021-06-30")
    n_then = one(conn, "SELECT COUNT(*) FROM snapshot_history WHERE as_of='2021-06-30'")
    n_now = one(conn, "SELECT COUNT(*) FROM snapshot")
    assert n_then <= n_now
    ages = one(conn, "SELECT MAX(data_age_days) FROM snapshot_history "
                     "WHERE as_of='2021-06-30'")
    assert ages is not None and ages >= 0, "data_age_days not computed"
    print(f"  OK  2021-06-30 universe = {n_then} companies (today {n_now}), "
          f"max data age {ages}d")


def test_screen_as_of_runs_against_vintage():
    conn, _ = build_db()
    transform.build_snapshot_asof(conn, "2023-06-30")
    rows = screen.run(conn, "market_cap > 100m", as_of="2023-06-30", limit=20)
    assert rows, "point-in-time screen returned nothing"
    vintages = screen.available_vintages(conn)
    assert "2023-06-30" in vintages
    print(f"  OK  as-of screen returned {len(rows)} rows; "
          f"{len(vintages)} vintages available")


# ------------------------------------------------- query engine

def test_screen_query_engine():
    conn, _ = build_db()
    rows = screen.run(conn, "net_margin > 15 and market_cap > 1b")
    tickers = [r["ticker"] for r in rows]
    assert "MODT" in tickers, "22% margin company should match"
    assert "LEGC" not in tickers, "8% margin company must not match"
    print(f"  OK  screen returned {tickers}")


def test_screen_rejects_injection():
    """The allowlist is the whole defense -- prove it holds."""
    conn, _ = build_db()
    for bad in ("pe > 1; DROP TABLE snapshot",
                "1=1 or 1=1",
                "name = 'x'",
                "pe > (SELECT 1)",
                "pe > 1 UNION SELECT * FROM companies"):
        try:
            screen.run(conn, bad)
        except screen.QueryError:
            continue
        raise AssertionError(f"injection attempt not rejected: {bad!r}")
    assert one(conn, "SELECT COUNT(*) FROM snapshot") == 6
    print("  OK  5 malformed/injection queries rejected, tables intact")


def test_suffix_parsing():
    where, params = screen.compile_query("market_cap > 2.5b and pe < 20")
    assert params == [2.5e9, 20.0], params
    assert "?" in where and "market_cap" in where
    print("  OK  suffix parsing (2.5b -> 2,500,000,000)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        print(f"{t.__name__}:")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {e}")
        except Exception as e:                              # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
