"""
Web layer smoke tests. Run: python tests/test_web.py

Status codes are not enough -- a 200 that renders an empty table looks fine to
curl and is broken to a user. These assert on content.

Requires: pip install fastapi uvicorn jinja2 httpx
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, transform                          # noqa: E402
from tests.make_fixtures import write_all, seed_db          # noqa: E402

_CACHE = {}


def client():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "web.db")

        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        transform.build_history(conn, transform.month_ends("2022-03-31", "2025-03-31"))
        conn.close()

        # app reads SCREENER_DB at request time, so set it before importing
        os.environ["SCREENER_DB"] = dbpath
        from fastapi.testclient import TestClient
        from web.app import app
        _CACHE["client"] = TestClient(app)
    return _CACHE["client"]


def test_screener_page_renders():
    r = client().get("/screener")
    assert r.status_code == 200
    html = r.text
    assert "Screen US equities" in html
    assert "MODT" in html, "default screen returned no companies"
    assert 'name="as_of"' in html, "vintage selector missing"
    assert "As of 2022-03-31" in html, "vintages not offered in dropdown"
    print("  OK  index renders, vintage selector populated")


def test_results_partial_is_a_fragment():
    """HTMX swaps innerHTML -- the partial must NOT be a full document."""
    r = client().get("/results", params={"q": "roe > 15"})
    assert r.status_code == 200
    assert "<table" in r.text
    assert "<!DOCTYPE" not in r.text, "partial returned a whole page"
    assert "<header" not in r.text
    print("  OK  /results returns a bare fragment")


def test_results_filter_actually_filters():
    c = client()
    wide = c.get("/results", params={"q": ""}).text
    narrow = c.get("/results", params={"q": "net_margin > 25"}).text
    assert "LEGC" in wide, "unfiltered screen should include the low-margin company"
    assert "LEGC" not in narrow, "filter did not exclude the low-margin company"
    assert "BANQ" in narrow, "28% margin company should survive the filter"
    print("  OK  filters change the result set")


def test_csv_export_matches_the_results_table():
    c = client()
    r = c.get("/results.csv", params={"q": "net_margin > 25"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="us-screener-' in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "Ticker,Company,Mkt cap,Price,P/E,P/B,ROE,Net mgn,D/E,Rev CAGR"
    assert "LEGC" not in r.text, "CSV export must respect the same filter as /results"
    assert "BANQ" in r.text
    print(f"  OK  CSV export: {len(lines) - 1} rows, header matches the results table")


def test_csv_export_rejects_bad_query_with_400_not_a_broken_file():
    c = client()
    r = c.get("/results.csv", params={"q": "pe > 1; DROP TABLE snapshot"})
    assert r.status_code == 400, "a malformed query must not silently download an empty/broken CSV"
    print("  OK  a bad query 400s instead of downloading a broken file")


def test_bad_query_shows_error_not_500():
    r = client().get("/results", params={"q": "hack > 1"})
    assert r.status_code == 200, "a bad query should not 500"
    assert "unknown field" in r.text
    print("  OK  bad query renders an inline error")


def test_point_in_time_changes_the_numbers():
    c = client()
    live = c.get("/results", params={"q": "", "order": "market_cap"}).text
    old = c.get("/results", params={"q": "", "as_of": "2022-03-31"}).text
    assert "as of" in old, "vintage footnote missing"
    assert live != old, "point-in-time view identical to live view"
    print("  OK  as-of view differs from live view")


def test_company_page_has_history_and_provenance():
    r = client().get("/company/MODT")
    assert r.status_code == 200
    html = r.text
    assert "Modern Tagger Inc" in html
    assert "Financials" in html
    assert "2024" in html, "annual history missing years"
    assert "RevenueFromContractWithCustomer" in html, "tag provenance missing"
    assert "<svg" in html, "revenue chart missing"
    print("  OK  company page: history, chart, tag provenance")


def test_company_page_shows_dashes_for_undefined_ratios():
    """NEGE has negative equity -- ROE must render as a dash, not a number."""
    r = client().get("/company/NEGE")
    assert r.status_code == 200
    assert "—" in r.text, "no em-dash placeholders found"
    import re
    block = re.search(r'<span class="k">ROE</span>\s*<span class="v[^"]*">\s*([^<]*?)\s*</span>',
                      r.text)
    assert block, "ROE not found in the at-a-glance rail"
    assert "—" in block.group(1), f"ROE rendered as {block.group(1)!r}"
    print("  OK  negative-equity company shows dash for ROE")


def test_company_404():
    assert client().get("/company/NOSUCH").status_code == 404
    print("  OK  unknown ticker 404s")


def test_coverage_page():
    r = client().get("/coverage")
    assert r.status_code == 200
    assert "Metric coverage" in r.text
    assert "revenue" in r.text
    assert "%" in r.text
    print("  OK  coverage dashboard renders")


def test_json_api():
    c = client()
    j = c.get("/api/screen", params={"q": "net_margin > 20", "limit": 5}).json()
    assert j["count"] >= 1
    assert all(r["net_margin"] > 20 for r in j["results"])

    err = c.get("/api/screen", params={"q": "drop > 1"})
    assert err.status_code == 400 and "unknown field" in err.json()["error"]

    co = c.get("/api/company/MODT").json()
    assert co["snapshot"]["ticker"] == "MODT"
    assert len(co["annual"]) >= 3

    assert len(c.get("/api/vintages").json()["vintages"]) >= 3
    assert c.get("/healthz").json()["ok"] is True
    print("  OK  JSON API: screen, company, vintages, health, 400 on bad query")


def test_openapi_schema_is_valid():
    spec = client().get("/api/openapi.json").json()
    assert "/api/screen" in spec["paths"]
    assert "/api/company/{ticker}" in spec["paths"]
    print("  OK  OpenAPI schema generated")


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
