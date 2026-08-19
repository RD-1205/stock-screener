"""
Ticker belt tests. Run: python tests/test_tape.py

Most of these guard behaviours that are easy to regress and hard to notice:
the duplicated track that makes the loop seamless, the aria-hidden clone, the
accessibility fallbacks that replaced the pause button, and the rule that a
decorative strip must never be able to break the page it sits on.
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db                                     # noqa: E402
from tests.make_fixtures import write_all, seed_db          # noqa: E402

_CACHE = {}


def setup():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "tape.db")
        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        os.environ["SCREENER_DB"] = dbpath
        os.environ.pop("FINNHUB_API_KEY", None)
        from fastapi.testclient import TestClient
        from web.app import app
        _CACHE.update(client=TestClient(app), conn=conn, db=dbpath)
    return _CACHE["client"], _CACHE["conn"]


def tape_html(c, path="/"):
    html = c.get(path).text
    m = re.search(r'<div class="tape"[\s\S]*?</div>\s*</div>\s*<div class="tape-label"[\s\S]*?</div>\s*</div>', html)
    assert m, "tape markup not found on " + path
    return m.group(0)


# --------------------------------------------------------------- presence

def test_tape_renders_under_header_on_every_page():
    c, _ = setup()
    for path in ("/", "/screener", "/stocks", "/screens", "/stocks/MODT"):
        html = c.get(path).text
        assert 'class="tape"' in html, f"tape missing on {path}"
        # It must hang BELOW the header, not above it.
        assert html.index("site-header") < html.index('class="tape"'), \
            f"tape rendered above the header on {path}"
        assert html.index('class="tape"') < html.index('id="main"'), \
            f"tape rendered below main on {path}"
    print("  OK  tape sits between header and main on 5 page types")


def test_tape_is_server_rendered_with_real_prices():
    """It must be populated on first paint and work with JavaScript off."""
    c, _ = setup()
    t = tape_html(c)
    assert "MODT" in t, "no symbols server-rendered"
    assert re.search(r'data-tape-price>\s*[\d,]+\.\d\d', t), "no real price rendered"
    assert re.search(r'[▲▼]\d+\.\d\d%', t), "no change percentage rendered"
    print("  OK  belt server-renders symbols, prices and changes")


def test_items_are_real_links():
    """JS-only click handlers are invisible to crawlers and break middle-click."""
    c, _ = setup()
    t = tape_html(c)
    assert 'href="/stocks/MODT"' in t
    assert t.count("<a class=\"tape-item\"") >= 2
    print("  OK  every item is a real anchor")


# ------------------------------------------------------- seamless scrolling

def test_track_is_duplicated_for_a_seamless_loop():
    """The track translates -50%, so the list must appear exactly twice or the
    belt visibly jumps when it wraps."""
    c, _ = setup()
    t = tape_html(c)
    assert t.count('data-tape-item="MODT"') == 2, "symbol not duplicated exactly once"
    print("  OK  list duplicated exactly twice")


def test_duplicate_is_hidden_from_screen_readers():
    c, _ = setup()
    t = tape_html(c)
    items = re.findall(r'<a class="tape-item"[^>]*>', t)
    hidden = [i for i in items if 'aria-hidden="true"' in i]
    assert len(hidden) == len(items) // 2, (
        "exactly half the items should be aria-hidden; screen readers would "
        "otherwise announce every symbol twice")
    for h in hidden:
        assert 'tabindex="-1"' in h, "hidden clone must not be tab-focusable"
    print("  OK  duplicate clone is aria-hidden and not focusable")


def test_scroll_speed_scales_with_item_count():
    """Fixed duration means the belt gets faster as symbols are added."""
    c, _ = setup()
    t = tape_html(c)
    m = re.search(r"--tape-duration:\s*(\d+)s", t)
    assert m, "no duration set"
    count = int(re.search(r'data-count="(\d+)"', t).group(1))
    assert int(m.group(1)) == count * 4, "duration should scale with item count"
    print(f"  OK  duration scales with count ({count} items)")


# ------------------------------------------------------------ accessibility

def test_motion_preferences_disable_the_scroll():
    """These are the mechanisms that replaced the pause button we dropped."""
    c, _ = setup()
    css = c.get("/static/css/base.css").text
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert re.search(r"\.tape:hover \.tape-track[\s\S]{0,120}paused", css), \
        "no hover pause"
    assert re.search(r"\.tape:focus-within \.tape-track[\s\S]{0,120}paused", css), \
        "no keyboard-focus pause"
    assert '[data-motion="reduce"] .tape-track' in css, "no in-app motion override"
    assert "animation: none" in css, "reduced motion must stop the animation"
    print("  OK  hover pause, focus pause, OS + in-app reduced motion all present")


def test_reduced_motion_keeps_items_reachable():
    """With the animation off the belt must still be scrollable, and the
    duplicate hidden so the list doesn't read twice."""
    css = setup()[0].get("/static/css/base.css").text
    assert re.search(r'\[data-motion="reduce"\] \.tape-viewport[\s\S]{0,60}overflow-x: auto', css)
    assert re.search(r'\[data-motion="reduce"\] \.tape-track > \[aria-hidden="true"\][\s\S]{0,40}display: none', css)
    print("  OK  reduced motion leaves a scrollable, non-duplicated list")


# ------------------------------------------------------------------ labelling

def test_source_is_always_labelled():
    """The label is what stops delayed data being presented as live."""
    c, _ = setup()
    t = tape_html(c)
    assert "At close" in t, "no source label on the belt"
    print("  OK  belt labels its price source")


# -------------------------------------------------------------- resilience

def test_tape_never_breaks_the_page():
    """A decorative strip must not be able to take the layout down."""
    c, conn = setup()
    from web import app as webapp

    webapp._tape_cache.update(at=0.0, value=[])
    original = webapp.quotes.get
    webapp.quotes.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down"))
    try:
        r = c.get("/")
        assert r.status_code == 200, "page died when the quote provider failed"
        assert 'class="tape"' not in r.text, "empty tape should render nothing"
        assert "Screen the market" in r.text, "rest of the page should be intact"
    finally:
        webapp.quotes.get = original
        webapp._tape_cache.update(at=0.0, value=[])
    print("  OK  provider failure hides the belt, page still renders")


def test_tape_is_cached():
    """It renders on every page -- an uncached quote lookup would put a
    provider round-trip on the critical path of the whole site."""
    from web import app as webapp
    c, _ = setup()
    webapp._tape_cache.update(at=0.0, value=[])
    c.get("/")
    first = webapp._tape_cache["at"]
    assert first > 0, "cache not populated"
    c.get("/screener")
    assert webapp._tape_cache["at"] == first, "cache not reused across requests"
    print("  OK  tape data cached across requests")


def test_tape_js_loaded():
    c, _ = setup()
    assert "js/tape.js" in c.get("/").text
    js = c.get("/static/js/tape.js").text
    assert "data-tape-price" in js, "in-place price update missing"
    assert "watchlist" in js.lower(), "watchlist support missing"
    assert "visibilitychange" in js, "should stop polling hidden tabs"
    print("  OK  tape.js served with in-place updates, watchlist, tab pausing")


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
