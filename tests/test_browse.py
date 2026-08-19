"""
Browse hub tests: sector mapping, size bands, index membership, activity
sorting, and filter composition. Run: python tests/test_browse.py
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import browse, db                            # noqa: E402
from tests.make_fixtures import write_all, seed_db         # noqa: E402

_CACHE = {}
PAT = re.compile(r'/stocks/(\w+)">')


def setup():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "browse.db")
        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        os.environ["SCREENER_DB"] = dbpath
        os.environ.pop("FINNHUB_API_KEY", None)
        from fastapi.testclient import TestClient
        from web.app import app
        _CACHE.update(client=TestClient(app), conn=conn)
    return _CACHE["client"], _CACHE["conn"]


def tickers(c, path):
    x = c.get(path).text
    if "<tbody>" not in x:
        return []
    return PAT.findall(x[x.index("<tbody>"):x.index("</tbody>")])


# ------------------------------------------------------------ sector mapping

def test_sic_maps_to_sectors():
    """SIC is free on every filing; GICS is licensed. The division mapping is
    the SEC's own, with a few high-volume ranges promoted."""
    cases = [("6022", "Banking"), ("3674", "Semiconductors"),
             ("7372", "Software & IT services"), ("2834", "Biotech & Pharma"),
             ("3711", "Manufacturing"), ("5812", "Retail trade"),
             (None, "Unclassified"), ("garbage", "Unclassified")]
    for sic, expected in cases:
        label, slug = browse.sector_of(sic)
        assert label == expected, f"SIC {sic} -> {label}, expected {expected}"
        assert slug
    print("  OK  8 SIC codes map to the right sectors, overrides beat divisions")


def test_overrides_beat_broad_divisions():
    """3674 sits inside Manufacturing (2000-3999) but must resolve to
    Semiconductors, or the sector menu is one giant useless bucket."""
    assert browse.sector_of("3674")[0] == "Semiconductors"
    assert browse.sector_of("3711")[0] == "Manufacturing"
    print("  OK  narrow overrides take precedence over the broad division")


def test_sector_menu_has_counts():
    _, conn = setup()
    secs = browse.sectors(conn)
    assert secs, "no sectors found"
    assert all(s["count"] > 0 for s in secs), "empty sectors should be hidden"
    assert sum(s["count"] for s in secs) == 6, "every company should land somewhere"
    labels = {s["label"] for s in secs}
    assert "Banking" in labels and "Semiconductors" in labels
    print(f"  OK  {len(secs)} sectors with counts: {sorted(labels)}")


# --------------------------------------------------------------- size bands

def test_mcap_bands_partition_the_universe():
    _, conn = setup()
    bands = browse.mcap_bands(conn)
    assert bands, "no size bands"
    total = sum(b["count"] for b in bands)
    with_mcap = conn.execute(
        "SELECT COUNT(*) FROM snapshot WHERE market_cap IS NOT NULL").fetchone()[0]
    assert total == with_mcap, (
        f"bands cover {total} but {with_mcap} companies have a market cap -- "
        "the ranges must partition, not overlap or leave gaps")
    print(f"  OK  size bands partition {with_mcap} companies with no overlap")


# ----------------------------------------------------------------- indexes

def test_index_membership_filters():
    c, _ = setup()
    sp = tickers(c, "/stocks?index=sp500")
    dow = tickers(c, "/stocks?index=dow30")
    assert len(sp) == 4 and len(dow) == 2, f"sp500={sp} dow30={dow}"
    assert set(dow).issubset(set(sp) | {"BANQ"}), "dow members should be real"
    assert "DEPR" not in sp, "non-member leaked into the S&P 500 view"
    print(f"  OK  index filters work: sp500={sp}, dow30={dow}")


# ------------------------------------------------------------ activity sort

def test_default_sort_is_most_active():
    """Alphabet was never the question people arrive with."""
    c, conn = setup()
    default = tickers(c, "/stocks")
    active = tickers(c, "/stocks?sort=active")
    assert default == active, "default sort should be most active"
    assert default != sorted(default), "should not be alphabetical by default"

    vols = []
    for t in default:
        v = conn.execute(
            "SELECT volume FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (t,)).fetchone()
        vols.append(v[0] if v else 0)
    assert vols == sorted(vols, reverse=True), f"not ordered by volume: {vols}"
    print(f"  OK  default is most active, ordered by volume: {default[:4]}")


def test_gainers_and_losers_are_mirrored():
    c, _ = setup()
    up = tickers(c, "/stocks?sort=gainers")
    down = tickers(c, "/stocks?sort=losers")
    assert up and down
    assert up == list(reversed(down)), "gainers should be losers reversed"
    print(f"  OK  gainers {up[:3]} / losers {down[:3]}")


# ------------------------------------------------------- filter composition

def test_filters_compose():
    """sector AND size AND index in one query, not a route per combination."""
    c, _ = setup()
    sp = set(tickers(c, "/stocks?index=sp500"))
    both = set(tickers(c, "/stocks?index=sp500&sector=banking"))
    banking = set(tickers(c, "/stocks?sector=banking"))
    assert both == sp & banking, f"{both} != {sp & banking}"
    assert both, "composition returned nothing"
    print(f"  OK  index ∩ sector = {both}")


def test_sort_survives_filtering():
    c, _ = setup()
    a = tickers(c, "/stocks?index=sp500&sort=mcap")
    b = tickers(c, "/stocks?index=sp500&sort=gainers")
    assert set(a) == set(b), "same filter should return the same set"
    assert a != b, "sort should change the order"
    print("  OK  sorting reorders without changing the filtered set")


def test_active_filters_render_as_clearable_chips():
    c, _ = setup()
    html = c.get("/stocks?index=sp500&sector=banking").text
    chips = re.findall(r'pill pill-accent"[^>]*href="([^"]+)"', html)
    assert len(chips) == 2, f"expected 2 filter chips, got {len(chips)}"
    for href in chips:
        assert "/stocks" in href
    # Removing one chip must leave the other filter applied.
    assert any("sector=banking" in h for h in chips), "chip drops the wrong filter"
    print("  OK  active filters render as chips that clear individually")


def test_menu_links_are_real_hrefs():
    """Browsing must work without JavaScript and be crawlable."""
    c, _ = setup()
    html = c.get("/stocks").text
    menu = html[html.index('class="browse-menu"'):html.index('class="browse-results"')]
    assert "<h3>Activity</h3>" in menu and "<h3>Sector</h3>" in menu
    assert "<h3>Size</h3>" in menu and "<h3>Alphabetical</h3>" in menu
    assert 'onclick' not in menu, "menu should not depend on JS handlers"
    assert menu.count('href="/stocks') >= 20, "menu links missing"
    print("  OK  every menu entry is a plain crawlable link")


# --------------------------------------------------------------- home page

def test_home_shows_most_active():
    c, conn = setup()
    html = c.get("/").text
    assert "Most active today" in html
    assert "Volume" in html, "volume column missing"
    home = PAT.findall(html[html.index("<tbody>"):html.index("</tbody>")])
    assert home == tickers(c, "/stocks?sort=active")[:len(home)], (
        "home page order should match the most-active browse view")
    print(f"  OK  home leads with most active: {home[:4]}")


def test_activity_is_labelled_as_end_of_day():
    """We only hold closes, so 'most active' is as of the last close. Saying
    so is the difference between honest and wrong."""
    c, _ = setup()
    for path in ("/", "/stocks"):
        assert "not intraday" in c.get(path).text, f"{path} doesn't qualify activity"
    print("  OK  activity labelled as last close, not intraday")


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
