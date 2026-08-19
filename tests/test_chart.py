"""
Price chart tests: series fetching, downsampling, the API contract, and the
company page ordering. Run: python tests/test_chart.py
"""

import os
import re
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, quotes, series                     # noqa: E402
from tests.make_fixtures import write_all, seed_db          # noqa: E402

_CACHE = {}


def setup():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "chart.db")
        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        os.environ["SCREENER_DB"] = dbpath
        os.environ.pop("FINNHUB_API_KEY", None)     # exercise the fallback
        from fastapi.testclient import TestClient
        from web.app import app
        _CACHE.update(client=TestClient(app), conn=conn)
    return _CACHE["client"], _CACHE["conn"]


# ------------------------------------------------------------ downsampling

def test_downsample_keeps_last_in_bucket():
    """Last close, not an average.

    A weekly average of daily closes is a price that never traded and it
    smooths away exactly the highs and lows people look at a chart to find.
    """
    rows = [("2024-01-01", 10.0), ("2024-01-02", 11.0), ("2024-01-03", 12.0),
            ("2024-01-08", 20.0), ("2024-01-09", 21.0)]
    weekly = series.downsample(rows, "week")
    assert len(weekly) == 2, f"expected 2 weekly buckets, got {len(weekly)}"
    assert weekly[0] == ("2024-01-03", 12.0), "not the last close of week 1"
    assert weekly[1] == ("2024-01-09", 21.0)
    assert series.downsample(rows, "day") == rows, "day bucket must pass through"
    print("  OK  downsampling keeps the last close per bucket")


def test_downsample_handles_year_boundary():
    """ISO week keys, so late-December and early-January don't collide."""
    rows = [("2024-12-30", 5.0), ("2024-12-31", 6.0), ("2025-01-02", 7.0)]
    out = series.downsample(rows, "week")
    assert len(out) >= 1
    assert out[-1] == ("2025-01-02", 7.0)
    monthly = series.downsample(rows, "month")
    assert len(monthly) == 2, "December and January must be separate months"
    print("  OK  year boundary bucketed correctly")


def test_ranges_reduce_point_count():
    _, conn = setup()
    counts = {}
    for r in ("1y", "5y", "max"):
        pts, meta = series.fetch(conn, "MODT", r, today=date(2025, 6, 30))
        counts[r] = len(pts)
        assert meta["range"] == r
    assert counts["max"] <= counts["5y"], (
        f"max ({counts['max']}) should be no denser than 5y ({counts['5y']}) "
        "after monthly bucketing")
    assert all(v > 0 for v in counts.values())
    print(f"  OK  point counts by range: {counts}")


def test_short_range_falls_back_rather_than_drawing_nothing():
    """Fixture prices are weekly, so a 1-month window can contain <2 points.

    An empty chart box looks broken. Falling back to the full history is the
    honest degradation.
    """
    _, conn = setup()
    pts, _ = series.fetch(conn, "MODT", "1m", today=date(2030, 1, 1))
    assert len(pts) >= 2, "sparse range should fall back to full history"
    print("  OK  sparse range falls back instead of rendering an empty chart")


# ------------------------------------------------------------- API contract

def test_chart_endpoint_shape():
    c, _ = setup()
    d = c.get("/api/chart/MODT?range=1y").json()
    assert isinstance(d["points"], list) and d["points"]
    p = d["points"][0]
    assert isinstance(p, list) and len(p) == 2, (
        "points must be [date, close] arrays -- objects are ~40% larger")
    assert isinstance(p[0], str) and len(p[0]) == 10, "date should be YYYY-MM-DD"
    assert isinstance(p[1], (int, float))
    for key in ("last", "change_pct", "low", "high", "range", "count"):
        assert key in d, f"missing {key}"
    assert d["low"] <= d["last"] <= d["high"]
    print(f"  OK  /api/chart returns {d['count']} array points + summary")


def test_chart_endpoint_bad_inputs():
    c, _ = setup()
    assert c.get("/api/chart/NOSUCHTICKER").status_code == 404
    # An unknown range must not 500 -- it falls back to the default.
    d = c.get("/api/chart/MODT?range=banana").json()
    assert d["range"] == series.DEFAULT_RANGE
    print("  OK  unknown ticker 404s, unknown range falls back to default")


def test_quote_endpoint_labels_its_source():
    """The label is what stops delayed data being presented as live."""
    c, _ = setup()
    d = c.get("/api/quote?symbols=MODT,BANQ").json()
    assert d["provider"] == "close", "no API key set, should fall back to close"
    assert d["label"] == "At close"
    assert "MODT" in d["quotes"]
    q = d["quotes"]["MODT"]
    assert q["price"] > 0 and q["label"] == "At close" and q["as_of"]
    assert c.get("/api/quote").json()["quotes"] == {}
    print("  OK  quotes fall back to close and label the source")


def test_quote_provider_switches_on_key():
    assert quotes.provider() == "close"
    os.environ["FINNHUB_API_KEY"] = "test-key"
    try:
        assert quotes.provider() == "finnhub"
        assert quotes.label() == "Delayed 15 min"
    finally:
        os.environ.pop("FINNHUB_API_KEY")
    print("  OK  provider switches to finnhub when a key is present")


# ----------------------------------------------------------- page structure

def test_price_chart_comes_before_the_ratios():
    """R3.1: the share price graph is the first thing on the page."""
    c, _ = setup()
    html = c.get("/stocks/MODT").text
    chart_at = html.index('data-chart="MODT"')
    # Compare against section anchors, not headings -- the sub-nav mentions
    # every section name above the chart by design.
    for sid in ("financials", "ratios", "balance", "provenance"):
        assert chart_at < html.index(f'<section id="{sid}"'), \
            f"price chart must precede the {sid} section"
    # The at-a-glance rail is the one thing allowed above the chart.
    assert html.index('class="keystat"') < chart_at
    print("  OK  key stats then chart, then financials, ratios and balance sheet")


def test_company_header_shows_price_and_source():
    c, _ = setup()
    html = c.get("/stocks/MODT").text
    assert "At close" in html, "price source label missing from header"
    assert "data-chart-legend" in html and "data-chart-canvas" in html
    for r in ("1m", "6m", "1y", "5y", "10y", "max"):
        assert f'data-range="{r}"' in html, f"range {r} missing"
    assert 'data-range="1y"\n                aria-pressed="true"' in html or \
           'aria-pressed="true"' in html, "no default range selected"
    print("  OK  header shows price + label, all 6 ranges present")


def test_chart_script_only_on_company_pages():
    """The library is ~45 KB. Other routes shouldn't pay for it."""
    c, _ = setup()
    assert "js/chart.js" in c.get("/stocks/MODT").text
    for path in ("/", "/screener", "/stocks", "/screens"):
        assert "js/chart.js" not in c.get(path).text, f"{path} loads chart.js"
    print("  OK  chart.js loads only on company pages")


def test_missing_prices_degrades_gracefully():
    """A company with no price history must explain itself, not render an
    empty chart frame."""
    c, conn = setup()
    conn.execute("UPDATE companies SET ticker='NOPX' WHERE cik=100003")
    conn.execute("UPDATE snapshot SET ticker='NOPX' WHERE cik=100003")
    conn.commit()
    html = c.get("/stocks/NOPX").text
    assert "No price history yet" in html
    assert 'data-chart="NOPX"' not in html, "should not render an empty chart"
    conn.execute("UPDATE companies SET ticker='DEPR' WHERE cik=100003")
    conn.execute("UPDATE snapshot SET ticker='DEPR' WHERE cik=100003")
    conn.commit()
    print("  OK  missing price history shows an explanation, not an empty frame")


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


# ------------------------------------------------ company page restructure

def test_metric_boxes_replaced_by_dense_rail():
    c, _ = setup()
    html = c.get("/stocks/MODT").text
    assert 'class="stat"' not in html, "old metric card grid still present"
    assert html.count('class="keystat"') >= 6, "at-a-glance rail missing"
    for label in ("Mkt cap", "P/E", "P/B", "EPS"):
        assert f">{label}<" in html, f"{label} not in the at-a-glance rail"
    print("  OK  metric boxes replaced by a dense key-stat rail")


def test_growth_shown_against_figures():
    c, _ = setup()
    html = c.get("/stocks/MODT").text
    assert re.search(r'class="g (up|down|flat)"', html), "no growth cells"
    assert "year on year" in html, "growth basis not explained"
    print("  OK  YoY growth rendered inside value cells")


def test_ratio_history_is_per_period():
    """Each column must be computed from that year's own inputs. If ratios were
    read off today's snapshot every column would be identical."""
    _, conn = setup()
    from screener import analysis
    r = analysis.ratios(conn, 100001)
    names = [row["metric"] for row in r["rows"]]
    for wanted in ("roe", "roce", "debt_to_equity", "net_margin"):
        assert wanted in names, f"{wanted} missing from ratio history"
    assert len(r["periods"]) >= 3, "need multiple year columns"
    assert all(len(row["vals"]) == len(r["periods"]) for row in r["rows"])
    print(f"  OK  ratio history: {len(names)} ratios x {len(r['periods'])} years")


def test_quarterly_toggle_returns_a_fragment():
    c, _ = setup()
    r = c.get("/stocks/MODT/statement?period=Q")
    assert r.status_code == 200
    assert "<!DOCTYPE" not in r.text, "toggle must return a bare fragment"
    assert "<table" in r.text
    assert c.get("/stocks/NOSUCH/statement").status_code == 404
    print("  OK  annual/quarterly toggle returns an HTMX fragment")


def test_quarterly_growth_compares_year_over_year():
    """Sequential quarterly growth mostly measures seasonality, so the YoY
    column must look back four quarters, not one."""
    _, conn = setup()
    from screener import analysis
    q = analysis.statement(conn, 100001, "Q", limit=8)
    rev = next(r for r in q["rows"] if r["metric"] == "revenue")
    assert all(g is None for g in rev["yoy"][:4]), "YoY should need 4 prior quarters"
    assert any(g is not None for g in rev["yoy"][4:]), "no YoY computed"
    assert any(g is not None for g in rev["qoq"][1:]), "QoQ not computed"
    print("  OK  quarterly YoY looks back 4 quarters, QoQ looks back 1")


def test_growth_refuses_sign_changes():
    from screener import analysis
    assert analysis._pct_change(2.0, -1.0) is None, "growth across a sign change"
    assert analysis._pct_change(2.0, 0) is None
    assert abs(analysis._pct_change(115.0, 100.0) - 15.0) < 1e-9
    print("  OK  growth returns None across sign changes and zero bases")


def test_peers_are_same_industry():
    _, conn = setup()
    from screener import analysis
    peers = analysis.peers(conn, 100001)
    assert peers, "no peers found"
    assert all(p["ticker"] != "MODT" for p in peers), "company is its own peer"
    print(f"  OK  peers: {[p['ticker'] for p in peers][:5]}")
