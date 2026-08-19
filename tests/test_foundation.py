"""
Foundation tests: layout shell, theming, navigation, static pages, machine
routes. Run: python tests/test_foundation.py

These cover the things that are invisible when they work and embarrassing when
they don't -- a theme flash on load, a footer with no links, a bare 404, a
sitemap that indexes pages we told robots.txt to skip.
"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db                                     # noqa: E402
from tests.make_fixtures import write_all, seed_db          # noqa: E402

_CACHE = {}


def client():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "found.db")
        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        conn.close()
        os.environ["SCREENER_DB"] = dbpath
        from fastapi.testclient import TestClient
        from web.app import app
        _CACHE["client"] = TestClient(app)
    return _CACHE["client"]


# ------------------------------------------------------------------ theming

def test_theme_applied_before_paint():
    """The no-flash script must be inline in <head>, before any stylesheet.

    If it loads as an external file the browser paints the default theme first
    and a dark-to-light flash on every navigation is the cheapest possible way
    to make a site feel broken.
    """
    html = client().get("/").text
    head = html[:html.index("</head>")]
    assert "localStorage" in head and "data-theme" in head, "no-flash init missing"
    script_at = head.index("ui.theme")
    css_at = head.index("tokens.css")
    assert script_at < css_at, "theme script must run before the stylesheet loads"
    print("  OK  theme applied inline before stylesheets")


def test_both_themes_and_motion_defined():
    css = client().get("/static/css/tokens.css").text
    assert '[data-theme="light"]' in css and '[data-theme="dark"]' in css
    for token in ("--accent", "--up", "--down", "--surface-1", "--text-2"):
        assert css.count(token) >= 2, f"{token} not defined in both themes"
    assert "prefers-reduced-motion" in css, "OS motion preference not honoured"
    assert '[data-motion="reduce"]' in css, "in-app motion override missing"
    print("  OK  light + dark complete, both reduced-motion paths present")


def test_tabular_numerals():
    """Without this, columns of numbers jitter and the data looks untrustworthy."""
    assert "tabular-nums" in client().get("/static/css/base.css").text
    print("  OK  tabular numerals enforced")


# ------------------------------------------------------------------- layout

def test_header_and_nav():
    html = client().get("/screener").text
    assert 'class="site-header"' in html
    for href, label in (("/screener", "Screener"), ("/stocks", "Stocks")):
        assert f'href="{href}"' in html and label in html
    assert 'aria-current="page"' in html, "active nav item not marked"
    assert "data-theme-toggle" in html and "data-search-trigger" in html
    print("  OK  header renders with nav, active state, theme + search controls")


def test_skip_link_is_first_focusable():
    html = client().get("/").text
    body = html[html.index("<body>"):]
    assert body.index("skip-link") < body.index("site-header")
    print("  OK  skip link precedes the header")


def test_footer_links_are_real():
    """The footer is how ~5,000 company pages get discovered."""
    html = client().get("/").text
    for href in ("/methodology", "/terms", "/privacy", "/disclaimer",
                 "/stocks", "/screens", "/lists"):
        assert f'href="{href}"' in html, f"footer missing {href}"
    assert 'href="/stocks/MODT"' in html, "no company links in footer"
    assert "Not investment advice" in html
    print("  OK  footer has legal, hub and company links + disclaimer")


# -------------------------------------------------------------------- pages

def test_landing_shows_real_data_only():
    html = client().get("/").text
    assert "Screen the market as it actually was" in html
    assert 'action="/search"' in html, "hero search form missing"
    assert "MODT" in html, "largest-companies table empty"
    print("  OK  landing renders hero search + real company data")


def test_stocks_hub_paginates_and_filters():
    c = client()

    def table_of(path):
        # Scope to the table body -- the footer lists popular tickers on every
        # page, so a page-wide substring check would always find them.
        html = c.get(path).text
        return html[html.index("<tbody>"):html.index("</tbody>")]

    assert "MODT" in table_of("/stocks")
    m = table_of("/stocks?letter=M")
    assert "MODT" in m, "M filter dropped a matching company"
    assert "BANQ" not in m, "M filter leaked a non-matching company"
    assert "BANQ" in table_of("/stocks?letter=B")
    print("  OK  /stocks lists, filters by letter")


def test_search_matches_ticker_and_name():
    c = client()
    assert "MODT" in c.get("/search?q=MODT").text, "ticker search failed"
    assert "MODT" in c.get("/search?q=Modern").text, "name search failed"
    assert "No companies match" in c.get("/search?q=zzzznothing").text
    print("  OK  search matches ticker and name, empty state renders")


def test_screens_index_has_live_counts():
    html = client().get("/screens").text
    assert "Quality at a fair price" in html
    assert "matches" in html, "live match counts missing"
    print("  OK  /screens renders cards with live counts")


def test_static_pages_render():
    c = client()
    for slug, marker in (("methodology", "Known limitations"),
                         ("terms", "Limitation of liability"),
                         ("privacy", "What we collect"),
                         ("disclaimer", "Not investment advice"),
                         ("about", "Why it exists")):
        r = c.get(f"/{slug}")
        assert r.status_code == 200, f"/{slug} returned {r.status_code}"
        assert marker in r.text, f"/{slug} missing expected content"
    print("  OK  5 static pages render with real copy")


# ----------------------------------------------------------------- redirects

def test_legacy_company_url_redirects_permanently():
    r = client().get("/company/MODT", follow_redirects=False)
    assert r.status_code == 301, f"expected 301, got {r.status_code}"
    assert r.headers["location"] == "/stocks/MODT"
    assert client().get("/company/MODT").status_code == 200
    print("  OK  /company/{t} 301s to /stocks/{t}")


# ------------------------------------------------------------------- errors

def test_404_is_useful_not_bare():
    r = client().get("/definitely-not-a-page")
    assert r.status_code == 404
    html = r.text
    assert 'action="/search"' in html, "404 should offer search"
    assert 'href="/stocks"' in html, "404 should link to the hub"
    assert "noindex" in html, "404 must not be indexed"
    print("  OK  404 offers search + popular tickers, marked noindex")


# ---------------------------------------------------------- machine routes

def test_robots_and_sitemap_agree():
    c = client()
    robots = c.get("/robots.txt").text
    assert "Sitemap:" in robots
    assert "Disallow: /api/" in robots and "Disallow: /search" in robots

    xml = c.get("/sitemap.xml").text
    root = ET.fromstring(xml)                    # raises if malformed
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [e.text for e in root.iter(f"{ns}loc")]
    assert any(l.endswith("/stocks/MODT") for l in locs), "company pages missing"
    assert any(l.endswith("/methodology") for l in locs)

    # A URL in the sitemap that robots.txt disallows is a contradiction that
    # search consoles flag as an error.
    disallowed = [l.split("Disallow: ")[1].strip()
                  for l in robots.splitlines() if l.startswith("Disallow:")]
    for loc in locs:
        path = loc.split("://", 1)[1].split("/", 1)[1]
        path = "/" + path
        for bad in disallowed:
            # robots.txt Disallow is a literal prefix match, so "/screener?"
            # blocks the parameterised form while leaving bare /screener
            # crawlable. Compare literally -- normalising the "?" away would
            # wrongly flag that as a contradiction.
            assert not path.startswith(bad), \
                f"sitemap lists {path} but robots disallows {bad}"
    print(f"  OK  robots + sitemap agree ({len(locs)} URLs)")


def test_opensearch_is_valid_xml():
    ET.fromstring(client().get("/opensearch.xml").text)
    print("  OK  opensearch.xml is well-formed")


def test_screener_with_params_is_noindex():
    """Unbounded query params would otherwise generate an infinite crawl space."""
    c = client()
    assert "noindex" not in c.get("/screener").text
    assert "noindex" in c.get("/screener?q=roe > 5").text
    print("  OK  bare /screener indexable, parameterised is not")


def test_static_assets_served():
    c = client()
    for path in ("css/tokens.css", "css/base.css", "js/app.js"):
        r = c.get(f"/static/{path}")
        assert r.status_code == 200 and len(r.text) > 500, f"{path} not served"
    print("  OK  css + js assets served")


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
