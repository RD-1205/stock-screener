"""
FastAPI + HTMX web app.

One process, no build step, no CORS. Every page is server-rendered HTML; the
interactive bits (running a screen, sorting, switching vintage) swap an HTML
fragment in via HTMX rather than shipping a client-side app.

The JSON API under /api is the same data through the same code paths, so when
you do want a React frontend later, it's already there.

Run:  python -m screener.cli serve
      uvicorn web.app:app --reload
Docs: http://127.0.0.1:8000/api/docs
"""

import math
import os
import sys
import time
from datetime import date
from urllib.parse import quote_plus

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import (analysis, browse, db, quotes, screen, sentiment,  # noqa: E402
                      series, transform)
from screener.concepts import METRICS                        # noqa: E402
from web.content import legal                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

SITE_NAME = os.environ.get("SITE_NAME", "us-screener")
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

app = FastAPI(
    title="us-screener",
    description="US equity fundamentals screener on free SEC EDGAR XBRL data.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

NAV = [
    ("/screener", "Screener"),
    ("/stocks", "Stocks"),
    ("/screens", "Screens"),
    ("/lists", "Lists"),
    ("/coverage", "Coverage"),
]

EXAMPLES = [
    ("Quality at a fair price", "roe > 15 and pe < 25 and debt_to_equity < 1"),
    ("Cheap and growing", "pe < 20 and revenue_growth > 10"),
    ("High margin compounders", "net_margin > 20 and revenue_growth > 12"),
    ("Deep value", "pb < 1.5 and pe < 12"),
    ("Cash generators", "fcf > 500m and market_cap > 2b"),
]


def slugify(label):
    return label.lower().replace(" ", "-").replace("/", "")


# Curated screens keyed by slug so /screens (gallery) and /screens/{slug}
# (indexable, pre-run page) share one source of truth -- see docs/SITEMAP.md,
# "curated screens live at /screens/{slug} precisely so there's a canonical
# indexable version" of what would otherwise be a noindexed ?q= URL.
SCREENS = {slugify(label): (label, query) for label, query in EXAMPLES}

# Curated "top 50 by metric" pages. Each is one ORDER BY against `snapshot`,
# and the SEO case for them is real -- "highest roe stocks" is a genuine
# search term with no good free answer. `positive` guards ratios where a
# negative value is either meaningless (P/E, P/B) or a negative-equity
# artifact (D/E) rather than a legitimate "lowest".
LISTS = {
    "highest-roe-stocks": {
        "title": "Highest ROE stocks", "column": "roe", "direction": "desc",
        "kind": "pct", "positive": False,
        "blurb": "Ranked by return on equity, trailing twelve months.",
    },
    "lowest-pe-stocks": {
        "title": "Lowest P/E stocks", "column": "pe", "direction": "asc",
        "kind": "num", "positive": True,
        "blurb": "Ranked by price-to-earnings, lowest first. Loss-making "
                 "companies (no meaningful P/E) are excluded, not shown as "
                 "cheapest.",
    },
    "highest-net-margin-stocks": {
        "title": "Highest net margin stocks", "column": "net_margin",
        "direction": "desc", "kind": "pct", "positive": False,
        "blurb": "Ranked by net income as a share of revenue.",
    },
    "highest-revenue-growth-stocks": {
        "title": "Highest revenue growth stocks", "column": "revenue_cagr_3y",
        "direction": "desc", "kind": "pct", "positive": False,
        "blurb": "Ranked by 3-year revenue CAGR.",
    },
    "lowest-debt-to-equity-stocks": {
        "title": "Lowest debt-to-equity stocks", "column": "debt_to_equity",
        "direction": "asc", "kind": "num", "positive": True,
        "blurb": "Ranked by debt-to-equity, lowest first.",
    },
    "largest-companies": {
        "title": "Largest companies by market cap", "column": "market_cap",
        "direction": "desc", "kind": "money", "positive": False,
        "blurb": "Ranked by market capitalization.",
    },
    "highest-fcf-stocks": {
        "title": "Highest free cash flow stocks", "column": "fcf_ttm",
        "direction": "desc", "kind": "money", "positive": False,
        "blurb": "Ranked by trailing twelve-month free cash flow.",
    },
    "lowest-pb-stocks": {
        "title": "Lowest P/B stocks", "column": "pb", "direction": "asc",
        "kind": "num", "positive": True,
        "blurb": "Ranked by price-to-book, lowest first.",
    },
}

TABLE_COLS = [
    ("ticker", "Ticker", "text"),
    ("name", "Company", "text"),
    ("market_cap", "Mkt cap", "money"),
    ("price", "Price", "num"),
    ("pe", "P/E", "num"),
    ("pb", "P/B", "num"),
    ("roe", "ROE", "pct"),
    ("net_margin", "Net mgn", "pct"),
    ("debt_to_equity", "D/E", "num"),
    ("revenue_cagr_3y", "Rev CAGR", "pct"),
]

SELECT = ", ".join(c[0] for c in TABLE_COLS)


# ------------------------------------------------------------------ helpers

def get_conn():
    return db.connect(os.environ.get("SCREENER_DB", "screener.db"))


def fmt_money(v):
    if v is None:
        return "—"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v/div:,.2f}{suf}"
    return f"{v:,.0f}"


def fmt_num(v, dp=2):
    return "—" if v is None else f"{v:,.{dp}f}"


def fmt_pct(v):
    return "—" if v is None else f"{v:,.1f}%"


templates.env.filters["money"] = fmt_money
templates.env.filters["num"] = fmt_num
templates.env.filters["pct"] = fmt_pct


def render_cell(value, kind):
    return {"money": fmt_money, "pct": fmt_pct,
            "num": fmt_num, "text": lambda v: v if v else "—"}[kind](value)


templates.env.globals["render_cell"] = render_cell


# ---- template globals -----------------------------------------------------
# Static values go straight in. `footer_tickers` is a callable so it can be
# cached -- the footer renders on every page and a per-request query for a list
# that changes nightly would be wasteful.

_ticker_cache = {"at": 0.0, "value": []}
FOOTER_TICKER_TTL = 300


def footer_tickers(limit=12):
    now = time.monotonic()
    if now - _ticker_cache["at"] < FOOTER_TICKER_TTL and _ticker_cache["value"]:
        return _ticker_cache["value"]
    try:
        rows = get_conn().execute(
            "SELECT ticker FROM snapshot WHERE ticker IS NOT NULL "
            "AND market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT ?", (limit,)
        ).fetchall()
        value = [r[0] for r in rows]
    except Exception:                                   # noqa: BLE001
        value = []                                      # DB not initialised yet
    _ticker_cache.update(at=now, value=value)
    return value


_tape_cache = {"at": 0.0, "value": []}
TAPE_TTL = 45
TAPE_SIZE = 14


def tape_items():
    """Symbols + quotes for the belt under the header.

    Server-rendered so the strip is populated on first paint and works with
    JavaScript off. tape.js then swaps in the user's watchlist if they have
    one and refreshes prices in place.

    Cached because this renders on EVERY page -- an uncached quote lookup in
    the layout would put a provider round-trip on the critical path of the
    whole site.
    """
    now = time.monotonic()
    if now - _tape_cache["at"] < TAPE_TTL and _tape_cache["value"]:
        return _tape_cache["value"]

    items = []
    try:
        conn = get_conn()
        syms = [r[0] for r in conn.execute(
            "SELECT ticker FROM snapshot WHERE ticker IS NOT NULL "
            "AND market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT ?",
            (TAPE_SIZE,))]
        quoted = quotes.get(conn, syms)
        for s in syms:
            q = quoted.get(s)
            if q:
                items.append({"ticker": s, "price": q["price"],
                              "change_pct": q.get("change_pct")})
    except Exception:                                   # noqa: BLE001
        items = []                                      # never break the layout

    _tape_cache.update(at=now, value=items)
    return items


templates.env.globals.update(
    site_name=SITE_NAME,
    site_url=SITE_URL,
    nav_items=NAV,
    footer_tickers=footer_tickers,
    tape_items=tape_items,
    tape_label=quotes.label,
    year=date.today().year,
)


def run_screen(conn, q, as_of, order, direction, limit):
    return screen.run(
        conn, q or "", order_by=order, desc=(direction == "desc"),
        limit=limit, select=SELECT, as_of=as_of or None,
    )


# ------------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Landing page.

    Currently the shell plus what we can honestly show from real data. The
    filing news feed (R1) lands in a later build step -- stubbing it with
    fake data now would just hide how much is left.
    """
    conn = get_conn()
    try:
        largest = browse.most_active(conn, limit=12)
        total = conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
    except Exception:                                   # noqa: BLE001
        largest, total = [], 0
    mood = mood_svg = None
    try:
        mood = sentiment.latest(conn)
        if mood:
            mood_svg = mood_gauge_svg(mood["composite"])
    except Exception:                                   # noqa: BLE001
        mood = mood_svg = None            # never break the landing page over this

    return templates.TemplateResponse(request, "pages/home.html", {
        "largest": largest, "total": total, "examples": EXAMPLES, "mood": mood,
        "mood_svg": mood_svg,
    })


@app.get("/screener", response_class=HTMLResponse)
def screener(request: Request,
             q: str = "",
             as_of: str = "",
             order: str = "market_cap",
             dir: str = "desc",
             limit: int = 50):
    conn = get_conn()
    vintages = screen.available_vintages(conn)
    error, rows = None, []
    try:
        rows = run_screen(conn, q, as_of, order, dir, limit)
    except screen.QueryError as e:
        error = str(e)

    return templates.TemplateResponse(request, "index.html", {
        "q": q, "as_of": as_of, "order": order,
        "dir": dir, "limit": limit, "rows": rows, "error": error,
        "cols": TABLE_COLS, "examples": EXAMPLES, "vintages": vintages,
        "fields": sorted(set(screen.COLUMNS)),
        "count": conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0],
        # /screener?q=... has unbounded parameter combinations, so only the
        # bare page is indexable. Curated screens live at /screens/{slug}.
        "noindex": bool(q or as_of),
        "header_note": None,
    })


@app.get("/results", response_class=HTMLResponse)
def results(request: Request,
            q: str = "",
            as_of: str = "",
            order: str = "market_cap",
            dir: str = "desc",
            limit: int = 50):
    """HTMX partial -- just the table."""
    conn = get_conn()
    error, rows = None, []
    try:
        rows = run_screen(conn, q, as_of, order, dir, limit)
    except screen.QueryError as e:
        error = str(e)
    return templates.TemplateResponse(request, "_results.html", {
        "rows": rows, "error": error, "cols": TABLE_COLS,
        "q": q, "as_of": as_of, "order": order, "dir": dir, "limit": limit,
    })


@app.get("/company/{ticker}")
def company_legacy(ticker: str):
    """Permanent redirect to the new URL shape.

    /stocks/{ticker} matches how people search and how competitors rank, so
    it's worth the rename -- but the old path has to keep working forever, or
    any link that already exists breaks.
    """
    return RedirectResponse(f"/stocks/{ticker.upper()}", status_code=301)


@app.get("/stocks", response_class=HTMLResponse)
def stocks_index(request: Request, sector: str = "", band: str = "",
                 index: str = "", letter: str = "",
                 sort: str = browse.DEFAULT_SORT, page: int = 1):
    """Browse hub.

    Filters compose -- sector AND size AND index AND letter -- through one
    query rather than a route per combination. Defaults to most active, since
    "what's moving today" is the question people actually arrive with;
    alphabet is a fallback, not the primary axis.
    """
    conn = get_conn()
    try:
        data = browse.browse(conn, sector=sector or None, band=band or None,
                             index=index or None, letter=letter or None,
                             sort=sort, page=page)
        secs, bands, idxs = (browse.sectors(conn), browse.mcap_bands(conn),
                             browse.indexes(conn))
    except Exception:                                   # noqa: BLE001
        data = {"rows": [], "total": 0, "page": 1, "pages": 1}
        secs = bands = idxs = []

    current = {"sector": sector, "band": band, "index": index,
               "letter": letter, "sort": sort, "page": page}

    def url_with(**over):
        """Build a URL preserving the other filters. Keeps every menu link a
        real href, so browsing works without JavaScript and is crawlable."""
        merged = {**current, **over}
        parts = [f"{k}={quote_plus(str(v))}" for k, v in merged.items()
                 if v not in ("", None) and not (k == "page" and v == 1)
                 and not (k == "sort" and v == browse.DEFAULT_SORT)]
        return "/stocks" + ("?" + "&".join(parts) if parts else "")

    labels = {s["slug"]: s["label"] for s in secs}
    labels.update({b["slug"]: b["label"] for b in bands})
    labels.update({i["slug"]: i["label"] for i in idxs})

    chips = []
    for key, val in (("index", index), ("sector", sector), ("band", band),
                     ("letter", letter)):
        if val:
            chips.append((labels.get(val, val), url_with(**{key: "", "page": 1})))

    heading = labels.get(index) or labels.get(sector) or labels.get(band) or "All companies"

    return templates.TemplateResponse(request, "pages/stocks_index.html", {
        "data": data, "sectors": secs, "bands": bands, "indexes": idxs,
        "letters": [chr(c) for c in range(ord("A"), ord("Z") + 1)],
        "sector": sector, "band": band, "index": index, "letter": letter,
        "sort": sort, "sorts": browse.SORTS, "url_with": url_with,
        "active_filters": chips, "heading": heading,
        "subtitle": browse.SORTS.get(sort, ("", ""))[0],
    })


@app.get("/screens", response_class=HTMLResponse)
def screens_index(request: Request):
    """Curated screens.

    Two jobs: onboarding for people who don't know what to type, and indexable
    landing pages. Live match counts make them feel alive rather than static.
    """
    conn = get_conn()
    cards = []
    for slug, (label, query) in SCREENS.items():
        try:
            count = len(screen.run(conn, query, limit=500, select="ticker"))
        except Exception:                               # noqa: BLE001
            count = None
        cards.append({"label": label, "query": query, "count": count, "slug": slug})
    return templates.TemplateResponse(request, "pages/screens_index.html",
                                      {"cards": cards})


@app.get("/screens/{slug}", response_class=HTMLResponse)
def screen_detail(request: Request, slug: str,
                  order: str = "market_cap", dir: str = "desc", limit: int = 50):
    """A curated screen, pre-run, at a stable indexable URL.

    Unlike /screener?q=..., this has no arbitrary query parameter, so it's a
    real canonical page search engines can rank -- see docs/SITEMAP.md.
    """
    found = SCREENS.get(slug)
    if not found:
        raise HTTPException(404, "Screen not found")
    label, query = found

    conn = get_conn()
    error, rows = None, []
    try:
        rows = run_screen(conn, query, "", order, dir, limit)
    except screen.QueryError as e:
        error = str(e)

    return templates.TemplateResponse(request, "pages/screen_detail.html", {
        "label": label, "query": query, "slug": slug,
        "rows": rows, "error": error, "cols": TABLE_COLS,
        "q": query, "as_of": "", "order": order, "dir": dir, "limit": limit,
    })


@app.get("/lists", response_class=HTMLResponse)
def lists_index(request: Request):
    """Gallery of the curated per-metric list pages, same job as /screens:
    onboarding plus a hub other pages and the sitemap can link into."""
    return templates.TemplateResponse(request, "pages/lists_index.html", {
        "lists": [{"slug": s, **cfg} for s, cfg in LISTS.items()],
    })


@app.get("/lists/{slug}", response_class=HTMLResponse)
def list_detail(request: Request, slug: str):
    cfg = LISTS.get(slug)
    if not cfg:
        raise HTTPException(404, "List not found")

    col = cfg["column"]                     # from LISTS, not user input -- safe to interpolate
    where = f"{col} IS NOT NULL" + (f" AND {col} > 0" if cfg["positive"] else "")
    order = "DESC" if cfg["direction"] == "desc" else "ASC"
    rows = get_conn().execute(
        f"SELECT {SELECT} FROM snapshot WHERE {where} ORDER BY {col} {order} LIMIT 50"
    ).fetchall()

    return templates.TemplateResponse(request, "pages/list_detail.html", {
        "cfg": cfg, "slug": slug, "rows": rows, "cols": TABLE_COLS,
    })


@app.get("/stocks/{ticker}", response_class=HTMLResponse)
def company(request: Request, ticker: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM snapshot WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"{ticker.upper()} not found")

    history = annual_history(conn, row["cik"])
    tick = row["ticker"]

    # The price chart is the first thing on the page (R3.1), so the header
    # needs a quote and the chart needs to know whether there's anything to
    # draw. Both are cheap; the series itself is fetched by the client.
    quote = quotes.get(conn, [tick]).get(tick) if tick else None
    has_prices = bool(tick) and conn.execute(
        "SELECT 1 FROM prices WHERE ticker=? LIMIT 1", (tick,)).fetchone() is not None

    return templates.TemplateResponse(request, "company.html", {
        "c": row, "history": history,
        "quote": quote,
        "has_prices": has_prices,
        "statement": analysis.statement(conn, row["cik"], "FY"),
        "ratios": analysis.ratios(conn, row["cik"]),
        "balance": analysis.balance_sheet(conn, row["cik"]),
        "peers": analysis.peers(conn, row["cik"]),
        # The revenue bars keep their place, just further down where they
        # belong -- they answer a different question than the price chart.
        "revenue_chart": bar_chart([(y, h["revenue"]) for y, h in history if h["revenue"]]),
        "sources": conn.execute(
            "SELECT DISTINCT metric, source_concept FROM fundamentals "
            "WHERE cik=? ORDER BY metric", (row["cik"],)).fetchall(),
    })


@app.get("/stocks/{ticker}/statement", response_class=HTMLResponse)
def company_statement(request: Request, ticker: str, period: str = "FY"):
    """HTMX partial: the annual/quarterly toggle swaps just the table."""
    row = get_conn().execute(
        "SELECT cik FROM snapshot WHERE ticker=?", (ticker.upper(),)).fetchone()
    if not row:
        raise HTTPException(404, f"{ticker.upper()} not found")
    period = "Q" if period.upper() == "Q" else "FY"
    data = analysis.statement(get_conn(), row["cik"], period,
                              limit=12 if period == "Q" else 10)
    return templates.TemplateResponse(request, "_partials/_statement.html",
                                      {"statement": data})


@app.get("/coverage", response_class=HTMLResponse)
def coverage(request: Request):
    """How much of the universe resolves for each metric.

    The single most useful internal page you will build. Every low number is a
    missing entry in concepts.py, and you cannot find those by reading filings
    by hand -- there are 14,000 us-gaap tags and companies pick freely.
    """
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM companies WHERE cik IN "
                         "(SELECT DISTINCT cik FROM facts)").fetchone()[0] or 1
    rows = []
    for m in METRICS:
        got = conn.execute(
            "SELECT COUNT(DISTINCT cik) FROM fundamentals WHERE metric=?",
            (m.name,)).fetchone()[0]
        tags = conn.execute(
            "SELECT source_concept, COUNT(DISTINCT cik) n FROM fundamentals "
            "WHERE metric=? GROUP BY source_concept ORDER BY n DESC LIMIT 4",
            (m.name,)).fetchall()
        rows.append({
            "metric": m.name, "kind": m.kind, "got": got, "total": total,
            "pct": 100.0 * got / total,
            "tags": [(t["source_concept"], t["n"]) for t in tags],
            "candidates": len(m.concepts),
        })
    rows.sort(key=lambda r: r["pct"])
    return templates.TemplateResponse(request, "coverage.html", {
        "rows": rows, "total": total,
    })


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    """Plain search.

    Deliberately simple. The real ⌘K palette with fuzzy ranking is build step 2
    (R2); this exists so the header button, the 404 page and the hero form all
    have somewhere real to go from day one rather than a dead link.
    """
    rows = []
    term = q.strip()
    if term:
        like = f"%{term}%"
        try:
            rows = get_conn().execute(
                "SELECT ticker, name, sic_description, market_cap, price "
                "FROM snapshot WHERE ticker LIKE ? OR name LIKE ? "
                "ORDER BY CASE WHEN ticker = ? THEN 0 "
                "WHEN ticker LIKE ? THEN 1 ELSE 2 END, market_cap DESC LIMIT 50",
                (like, like, term.upper(), f"{term.upper()}%"),
            ).fetchall()
        except Exception:                               # noqa: BLE001
            rows = []
    return templates.TemplateResponse(request, "pages/search.html", {
        "q": term, "rows": rows, "noindex": True,
    })


# --------------------------------------------------------------- error pages

@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return templates.TemplateResponse(request, "pages/error.html", {
        "code": 404,
        "heading": "We couldn't find that page",
        "message": "The link may be wrong, or the company may not be in our "
                   "universe yet. Try a search.",
        "noindex": True,
    }, status_code=404)


@app.exception_handler(500)
async def server_error(request: Request, exc):
    """Deliberately does not touch the database -- an error page that queries
    the DB fails exactly when the DB is what broke."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Something went wrong</title>"
        "<style>body{background:#0c0b09;color:#efe9de;font-family:system-ui;"
        "display:grid;place-items:center;height:100vh;margin:0;text-align:center}"
        "a{color:#e0a83c}</style>"
        "<div><h1>Something went wrong</h1>"
        "<p>We've been notified. Try again in a moment.</p>"
        "<p><a href='/'>Back to the home page</a></p></div>",
        status_code=500,
    )


# ------------------------------------------------------------ machine routes

@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /search\n"
        "Disallow: /account/\n"
        "Disallow: /screener?\n"
        f"\nSitemap: {SITE_URL}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    """Flat sitemap for now. Split into a sitemap index once company pages
    push past 50,000 URLs -- see docs/SITEMAP.md §4."""
    urls = ["/", "/screener", "/stocks", "/screens", "/lists", "/coverage",
            "/methodology", "/about", "/terms", "/privacy", "/disclaimer"]
    urls += [f"/screens/{slug}" for slug in SCREENS]
    urls += [f"/lists/{slug}" for slug in LISTS]
    try:
        urls += [f"/stocks/{r[0]}" for r in get_conn().execute(
            "SELECT ticker FROM snapshot WHERE ticker IS NOT NULL "
            "ORDER BY market_cap DESC")]
    except Exception:                                   # noqa: BLE001
        pass

    today = date.today().isoformat()
    body = "".join(
        f"<url><loc>{SITE_URL}{u}</loc><lastmod>{today}</lastmod></url>"
        for u in urls
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        media_type="application/xml",
    )


@app.get("/opensearch.xml", include_in_schema=False)
def opensearch():
    """Lets a browser add the site as a search engine. Twenty lines, and it
    makes the product feel considerably more finished than it is."""
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        f"<ShortName>{SITE_NAME}</ShortName>"
        "<Description>Search US companies by ticker or name</Description>"
        f'<Url type="text/html" template="{SITE_URL}/search?q={{searchTerms}}"/>'
        "</OpenSearchDescription>",
        media_type="application/opensearchdescription+xml",
    )


# ------------------------------------------------------------------ JSON API

@app.get("/api/screen")
def api_screen(q: str = Query("", description="e.g. 'roe > 15 and pe < 25'"),
               as_of: str = Query("", description="vintage date, YYYY-MM-DD"),
               order: str = "market_cap",
               dir: str = "desc",
               limit: int = Query(50, le=500)):
    conn = get_conn()
    try:
        rows = run_screen(conn, q, as_of, order, dir, limit)
    except screen.QueryError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"query": q, "as_of": as_of or None, "count": len(rows),
            "results": [dict(r) for r in rows]}


@app.get("/api/company/{ticker}")
def api_company(ticker: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM snapshot WHERE ticker = ?",
                       (ticker.upper(),)).fetchone()
    if not row:
        raise HTTPException(404, f"{ticker.upper()} not found")
    return {"snapshot": dict(row),
            "annual": [{"year": y, **h} for y, h in annual_history(conn, row["cik"])]}


@app.get("/api/chart/{ticker}")
def api_chart(ticker: str, range: str = series.DEFAULT_RANGE):
    """Price series for the chart island.

    Points are arrays, not objects: [["2024-01-02", 185.64], ...] is about 40%
    smaller than the object form over a few thousand rows, and costs one map()
    on the client. At 10 years of history that's a real saving.
    """
    points, meta = series.fetch(get_conn(), ticker, range)
    if not points:
        raise HTTPException(404, f"No price history for {ticker.upper()}")
    return {"points": points, **meta}


@app.get("/api/quote")
def api_quote(symbols: str = Query("", description="comma-separated tickers")):
    """Quotes for the ticker tape. Delayed or at-close depending on config."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:40]
    if not syms:
        return {"provider": quotes.provider(), "label": quotes.label(), "quotes": {}}
    return {
        "provider": quotes.provider(),
        "label": quotes.label(),
        "quotes": quotes.get(get_conn(), syms),
    }


@app.get("/api/vintages")
def api_vintages():
    return {"vintages": screen.available_vintages(get_conn())}


@app.get("/api/fields")
def api_fields():
    return {"fields": sorted(set(screen.COLUMNS)),
            "operators": sorted(screen.OPERATORS),
            "suffixes": screen.SUFFIXES}


@app.get("/healthz")
def healthz():
    conn = get_conn()
    return {"ok": True,
            "companies": conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0],
            "vintages": len(screen.available_vintages(conn))}


# ------------------------------------------------------------------ data prep

HISTORY_METRICS = ["revenue", "gross_profit", "operating_income", "net_income",
                   "eps_diluted", "operating_cash_flow", "total_assets",
                   "total_equity"]


def annual_history(conn, cik, limit_years=10):
    rows = conn.execute(
        "SELECT period_end, metric, val FROM fundamentals "
        "WHERE cik=? AND period_type IN ('FY','INSTANT') ORDER BY period_end",
        (cik,),
    ).fetchall()
    by_year = {}
    for r in rows:
        if r["metric"] not in HISTORY_METRICS:
            continue
        by_year.setdefault(r["period_end"][:4], {})[r["metric"]] = r["val"]
    years = sorted(by_year)[-limit_years:]
    return [(y, {m: by_year[y].get(m) for m in HISTORY_METRICS}) for y in years]


def bar_chart(pairs, width=560, height=140):
    """Server-rendered SVG. No JS, no chart library, no CDN -- it just works."""
    pairs = [(y, v) for y, v in pairs if v is not None]
    if not pairs:
        return ""
    top = max(v for _, v in pairs) or 1
    n = len(pairs)
    bw = width / max(n, 1) * 0.6
    gap = width / max(n, 1)
    bars = []
    for i, (year, val) in enumerate(pairs):
        h = max(2.0, (val / top) * (height - 28))
        x = i * gap + (gap - bw) / 2
        y = height - h - 16
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" '
            f'rx="2" class="bar"><title>{year}: {fmt_money(val)}</title></rect>'
            f'<text x="{x + bw/2:.1f}" y="{height - 3}" class="lbl">{year}</text>'
        )
    return (f'<svg viewBox="0 0 {width} {height}" class="chart" '
            f'preserveAspectRatio="none">{"".join(bars)}</svg>')


# score 0-100 -> zone slug, matching screener.sentiment.ZONES exactly so the
# arc segments and the text label always agree with each other.
_MOOD_ZONE_SLUGS = ["extreme-fear", "fear", "neutral", "greed", "extreme-greed"]


def _polar(cx, cy, r, score):
    """A point on the gauge arc for a 0-100 score.

    Score 0 sits at the left end of the semicircle (180 deg, math
    convention), 100 at the right end (0 deg), 50 straight up (90 deg) --
    left-to-right reads fear-to-greed the way the linear bar already did.
    SVG y grows downward, so the y term is subtracted rather than added.
    """
    angle = math.radians(180 - (score / 100.0) * 180)
    return cx + r * math.cos(angle), cy - r * math.sin(angle)


def mood_gauge_svg(composite, width=300, height=180):
    """Server-rendered semicircle dial: 5 coloured zone bands, a needle at
    the current score. No JS, no chart library -- same approach as
    bar_chart() above."""
    cx, cy = width / 2, height - 20
    r_out, r_in = height - 40, height - 75

    bands = []
    for i, slug in enumerate(_MOOD_ZONE_SLUGS):
        lo, hi = i * 20, (i + 1) * 20
        x1o, y1o = _polar(cx, cy, r_out, lo)
        x2o, y2o = _polar(cx, cy, r_out, hi)
        x1i, y1i = _polar(cx, cy, r_in, lo)
        x2i, y2i = _polar(cx, cy, r_in, hi)
        bands.append(
            f'<path class="mood-arc-{slug}" d="'
            f'M {x1o:.1f} {y1o:.1f} '
            f'A {r_out:.1f} {r_out:.1f} 0 0 1 {x2o:.1f} {y2o:.1f} '
            f'L {x2i:.1f} {y2i:.1f} '
            f'A {r_in:.1f} {r_in:.1f} 0 0 0 {x1i:.1f} {y1i:.1f} Z"/>'
        )

    score = max(0.0, min(100.0, composite))
    nx, ny = _polar(cx, cy, r_out + 8, score)
    needle = (
        f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" class="mood-needle"/>'
        f'<circle cx="{cx}" cy="{cy}" r="6" class="mood-needle-hub"/>'
    )

    return (f'<svg viewBox="0 0 {width} {height}" class="mood-dial" '
            f'role="img" aria-label="Market mood: {score:.0f} out of 100">'
            f'{"".join(bands)}{needle}</svg>')


# ---------------------------------------------------------------- catch-all
# MUST stay last. FastAPI matches routes in declaration order, so a one-segment
# catch-all declared earlier would swallow /healthz, /robots.txt and friends.

@app.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
def static_page(request: Request, slug: str):
    """Terms, privacy, disclaimer, methodology, about."""
    page = legal.get(slug)
    if not page:
        raise HTTPException(404, "Page not found")
    title, desc, body, updated = page
    return templates.TemplateResponse(request, "pages/legal.html", {
        "page_title": title, "page_desc": desc, "body": body, "updated": updated,
    })
