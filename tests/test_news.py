"""
Homepage market-news feed tests. Run: python tests/test_news.py
"""

import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import db, news                                # noqa: E402
from tests.make_fixtures import write_all, seed_db           # noqa: E402

_CACHE = {}

RAW_ITEM = {
    "category": "business", "datetime": 1789430640, "headline": "US 20yr yield hits 5%",
    "id": 1, "image": "https://example.com/x.jpg", "related": "",
    "source": "Reuters", "summary": "...", "url": "https://example.com/article",
}


def setup():
    if "client" not in _CACHE:
        tmp = tempfile.mkdtemp()
        fixtures = os.path.join(tmp, "fixtures")
        write_all(fixtures)
        dbpath = os.path.join(tmp, "news.db")
        conn = db.connect(dbpath)
        db.init(conn)
        seed_db(conn, fixtures)
        os.environ["SCREENER_DB"] = dbpath
        os.environ.pop("FINNHUB_API_KEY", None)
        from fastapi.testclient import TestClient
        from web.app import app
        os.environ.pop("FINNHUB_API_KEY", None)
        _CACHE.update(client=TestClient(app), conn=conn)
    return _CACHE["client"], _CACHE["conn"]


# ------------------------------------------------------------- pure parsing

def test_clean_drops_items_missing_headline_or_url():
    raw = [
        RAW_ITEM,
        {**RAW_ITEM, "headline": ""},
        {**RAW_ITEM, "url": ""},
        {**RAW_ITEM, "headline": None},
    ]
    cleaned = news._clean(raw)
    assert len(cleaned) == 1, f"expected 1 usable item, got {len(cleaned)}"
    print("  OK  items missing a headline or url are dropped")


def test_clean_parses_the_timestamp():
    cleaned = news._clean([RAW_ITEM])
    dt = cleaned[0]["datetime"]
    assert dt == datetime.fromtimestamp(1789430640, tz=timezone.utc)
    print(f"  OK  unix timestamp parsed to {dt.isoformat()}")


def test_clean_survives_a_bad_timestamp():
    """A malformed datetime must not take out the whole item -- it's still
    a real headline with a real link, just missing an age."""
    cleaned = news._clean([{**RAW_ITEM, "datetime": "not-a-number"}])
    assert len(cleaned) == 1
    assert cleaned[0]["datetime"] is None
    print("  OK  a bad timestamp degrades to None instead of dropping the item")


def test_clean_never_reproduces_the_article_body():
    """Licensing rule: headline + source + timestamp + link only."""
    cleaned = news._clean([RAW_ITEM])
    assert "summary" not in cleaned[0] and "body" not in cleaned[0]
    print("  OK  only headline/source/url/datetime carried through, no article body")


# ------------------------------------------------------------------- general()

def test_no_key_returns_empty_without_a_network_call():
    os.environ.pop("FINNHUB_API_KEY", None)
    news._cache.clear()
    assert news.general() == []
    print("  OK  no FINNHUB_API_KEY -> empty feed, no crash")


def test_general_returns_cleaned_items():
    news._cache.clear()
    os.environ["FINNHUB_API_KEY"] = "test-key"
    original = news._fetch
    news._fetch = lambda category, key, timeout=8: [RAW_ITEM]
    try:
        items = news.general(limit=5)
        assert len(items) == 1
        assert items[0]["headline"] == "US 20yr yield hits 5%"
        assert items[0]["url"] == "https://example.com/article"
    finally:
        news._fetch = original
        os.environ.pop("FINNHUB_API_KEY", None)
        news._cache.clear()
    print("  OK  general() returns cleaned items from the provider")


def test_a_dead_provider_falls_back_to_the_last_good_cache():
    news._cache.clear()
    os.environ["FINNHUB_API_KEY"] = "test-key"
    original = news._fetch
    news._fetch = lambda category, key, timeout=8: [RAW_ITEM]
    try:
        first = news.general()
        assert first, "seed fetch should have populated the cache"
        news._cache["general"] = (0.0, news._cache["general"][1])  # force-expire
        news._fetch = lambda category, key, timeout=8: (_ for _ in ()).throw(
            RuntimeError("provider down"))
        second = news.general()
        assert second == first, "a dead provider should serve the last good cache, not crash"
    finally:
        news._fetch = original
        os.environ.pop("FINNHUB_API_KEY", None)
        news._cache.clear()
    print("  OK  provider failure falls back to the last cached headlines")


# --------------------------------------------------------------- homepage

def test_homepage_renders_headlines():
    c, _ = setup()
    from web import app as webapp

    original = webapp.news.general
    webapp.news.general = lambda *a, **k: [{
        "headline": "US 20yr yield hits 5%", "source": "Reuters",
        "url": "https://example.com/article",
        "datetime": datetime(2026, 9, 15, 2, 4, tzinfo=timezone.utc),
    }]
    try:
        r = c.get("/")
        assert r.status_code == 200
        assert "US 20yr yield hits 5%" in r.text
        assert 'href="https://example.com/article"' in r.text
        assert 'rel="noopener nofollow"' in r.text, "external news links must be nofollow"
    finally:
        webapp.news.general = original
    print("  OK  homepage renders a real headline linking out, nofollow")


def test_homepage_survives_a_dead_news_provider():
    c, _ = setup()
    from web import app as webapp

    original = webapp.news.general
    webapp.news.general = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down"))
    try:
        r = c.get("/")
        assert r.status_code == 200, "page died when the news provider failed"
        assert "No news feed configured" in r.text or "Market news" in r.text
    finally:
        webapp.news.general = original
    print("  OK  a dead news provider degrades to an empty state, page still renders")


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
