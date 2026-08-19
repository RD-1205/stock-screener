# Complete route inventory and link map

Every page the site will have, including the boring ones that get forgotten
until launch week. Plus what the landing page links to, and the indexation
rules that stop it becoming a crawl trap.

Legend: **Idx** = should search engines index it · **Auth** = requires login

---

# 1. Core product

| Route | Purpose | Idx | Auth |
|---|---|---|---|
| `/` | Landing — search, market pulse, filing news feed | ✅ | — |
| `/screener` | The tool. Full state in the query string | ⚠️ base only | — |
| `/stocks` | **A–Z hub of all companies.** Paginated, sector-filtered | ✅ | — |
| `/stocks/{ticker}` | Company overview, chart-first | ✅ | — |
| `/stocks/{ticker}/financials` | Statements, interactive | ✅ | — |
| `/stocks/{ticker}/shareholding` | 13F, 13D/G, insiders | ✅ | — |
| `/stocks/{ticker}/news` | Company filing events | ✅ | — |
| `/stocks/{ticker}/filings` | Filing history + restatement timeline | ✅ | — |
| `/stocks/{ticker}/peers` | Peer comparison | ✅ | — |
| `/news` | Full filing-news archive, paginated | ✅ | — |
| `/screens` | Gallery of curated screens | ✅ | — |
| `/screens/{slug}` | One screen, pre-run with live results | ✅ | — |
| `/lists/{slug}` | `highest-roe-stocks` etc., top 50 by metric | ✅ | — |
| `/compare/{a}-vs-{b}` | Side-by-side, **curated pairs only** | ⚠️ see §4 | — |
| `/backtest` | Replay a screen through history | ❌ | Pro |
| `/watchlist` | Saved stocks + news filtered to them | ❌ | — * |
| `/search` | Full-page search results | ❌ | — |

\* Watchlist works anonymously via `localStorage`, migrates on signup.

## Market-level pages — the gap in the current plan

The mood gauge needs somewhere to expand into (R6.3–R6.5), and sector pages
are natural SEO. Neither existed in the plan until now:

| Route | Purpose | Idx |
|---|---|---|
| `/markets` | Indices, breadth, sector performance, movers | ✅ |
| `/markets/movers` | Gainers, losers, most active | ✅ |
| `/mood` | **Sentiment detail** — gauge, all 7 components with z-scores, history chart, and the "screen as of past readings" hook | ✅ |
| `/sectors` | All sectors with aggregate metrics | ✅ |
| `/sectors/{slug}` | Companies in a sector + sector aggregates | ✅ |

`/mood` is worth building properly. It's the most linkable page on the site —
a transparent sentiment index with published methodology is exactly the kind of
thing finance blogs and Reddit link to, and it carries the point-in-time hook.

# 2. Learn — cheap programmatic SEO

| Route | Purpose | Idx |
|---|---|---|
| `/learn` | Glossary index | ✅ |
| `/learn/{term}` | `what-is-roe`, `price-to-book-ratio`, `13f-filing`… | ✅ |

Roughly 60–100 short definition pages. Each explains a term, shows the formula,
and — the part competitors don't do — **links to a live list of companies
ranked by that metric**. "What is ROE" is a real, high-volume search, and your
version ends with actual data instead of a wall of text.

Cheap to write, compounding traffic, and it gives every metric in the screener
a place to link its tooltip to.

# 3. Account, commercial, trust, legal

| Route | Purpose | Idx | Auth |
|---|---|---|---|
| `/login` `/signup` | Auth entry | ✅ (thin) | — |
| `/auth/{provider}/callback` | OAuth return | ❌ | — |
| `/logout` | POST only | ❌ | ✅ |
| `/forgot-password` `/reset-password` `/verify-email` | Only if you ship password auth | ❌ | — |
| `/onboarding` | First-run: pick interests, seed a watchlist | ❌ | ✅ |
| `/account` | Profile, appearance, **reduce motion** | ❌ | ✅ |
| `/account/screens` | Saved screens | ❌ | ✅ |
| `/account/alerts` | Alert rules | ❌ | ✅ |
| `/account/billing` | Plan, invoices, cancel | ❌ | ✅ |
| `/account/api` | API keys, usage | ❌ | ✅ |
| `/pricing` | Free vs Pro | ✅ | — |
| `/upgrade` → `/checkout/{success,cancel}` | Stripe flow | ❌ | ✅ |
| `/methodology` | Data sources, normalization, **known limitations** | ✅ | — |
| `/about` | Who built it and why | ✅ | — |
| `/contact` | Support form or email | ✅ | — |
| `/changelog` | What shipped, dated | ✅ | — |
| `/terms` `/privacy` `/disclaimer` | Required before taking money | ✅ | — |

**`/disclaimer` is not optional once you charge.** "Not investment advice, data
may contain errors, verify against primary filings" — linked from the footer of
every page, not buried.

# 4. Machine routes

| Route | Notes |
|---|---|
| `/robots.txt` | Disallow `/account/`, `/api/`, `/search`, parameterised `/screener` |
| `/sitemap.xml` | **Sitemap index**, not a flat file — see below |
| `/sitemap-stocks-{n}.xml` | Split at 50k URLs |
| `/sitemap-lists.xml` `/sitemap-learn.xml` `/sitemap-static.xml` | |
| `/feed.rss` | Filing news as RSS. Cheap, and it drives return visits |
| `/opensearch.xml` | Lets browsers add you as a search engine. ~20 lines, feels premium |
| `/api/docs` `/api/openapi.json` | Already built |
| `/healthz` | Extend to check **data freshness**, not just liveness |
| `/404` `/500` `/maintenance` | See §6 |

Google caps sitemaps at 50,000 URLs. With ~5,000 tickers × 5 tabs you're at
25k before lists and glossary, so build the index-of-sitemaps structure from
day one rather than retrofitting it.

---

# 5. Indexation rules — and the trap to avoid

**`/compare/{a}-vs-{b}` is a crawl trap.** 5,000 tickers is ~12.5 million
possible pairs. If those are all reachable and indexable, you generate millions
of near-identical thin pages, which is a textbook way to get the whole domain
demoted.

Rule: **only generate and link comparison pages for a curated set** — same
sector, both above a market-cap floor, roughly 2,000–5,000 pairs total. Every
other combination still *works* if someone constructs the URL, but returns
`noindex` and isn't linked from anywhere.

The same logic applies to `/screener?q=…`. Infinite parameter combinations, so
index the bare `/screener` page only and `noindex` anything with a query
string. Curated screens live at `/screens/{slug}` precisely so there's a
canonical indexable version.

| Pattern | Rule |
|---|---|
| `/stocks/*`, `/lists/*`, `/screens/*`, `/learn/*`, `/sectors/*` | Index, in sitemap |
| `/screener` bare | Index |
| `/screener?*` | `noindex, follow` |
| `/compare/*` curated | Index, in sitemap |
| `/compare/*` other | `noindex` |
| `/account/*`, `/watchlist`, `/backtest`, `/search` | `noindex`, robots-disallowed |

---

# 6. Hub pages — how deep pages actually get found

A sitemap tells search engines a URL exists. **Internal links are what make it
worth crawling.** Without hubs, most of your 5,000 company pages will sit
uncrawled regardless of what the sitemap says.

Three hubs do the work:

- **`/stocks`** — A–Z index, paginated, sector-filterable. Every company
  reachable within two clicks of the homepage.
- **`/sectors/{slug}`** — every company in a sector, giving each ticker a
  second, topically-relevant inbound link.
- **Footer** — top ~50 tickers, all screens, all lists, all sectors on every
  page.

Plus per-page cross-links: peers on company pages, "compare with…", and
glossary links from every metric tooltip.

---

# 7. Landing page — every clickable element

| Element | Destination |
|---|---|
| **Header** | |
| Logo | `/` |
| Screener | `/screener` |
| Stocks | `/stocks` |
| Screens | `/screens` |
| Markets | `/markets` |
| Learn | `/learn` |
| Search icon / `⌘K` | opens palette |
| Theme toggle | in-page |
| Sign in | `/login` |
| **Hero search** | |
| Select a result | `/stocks/{ticker}` |
| Enter with no selection | `/search?q=…` |
| Suggested tickers below | `/stocks/{ticker}` |
| **Market pulse band** | |
| S&P / Nasdaq values | `/markets` |
| Ticker tape item | `/stocks/{ticker}` |
| Tape source dropdown | in-page; "Watchlist" empty state → `/watchlist` |
| Mood gauge | `/mood` |
| Mood history callout | `/screener?as_of={date}` |
| Breadth numbers | `/markets/movers` |
| **News feed** | |
| Filter controls | in-page (HTMX) |
| Card ticker / company | `/stocks/{ticker}` |
| "Read filing" | EDGAR — external, `rel="noopener nofollow"` |
| "Full financials" | `/stocks/{ticker}/financials` |
| Card timestamp | `/stocks/{ticker}/news` |
| "See all news" | `/news` |
| **Preset screen cards** | `/screens/{slug}` |
| **Footer** | |
| Popular stocks (~50) | `/stocks/{ticker}` |
| All screens | `/screens/{slug}` |
| All lists | `/lists/{slug}` |
| Sectors | `/sectors/{slug}` |
| Methodology · About · Changelog · Contact | respective |
| Pricing · API docs | `/pricing`, `/api/docs` |
| Terms · Privacy · Disclaimer | respective |
| RSS icon | `/feed.rss` |

Two deliberate choices. **Everything on the landing page is a real `<a href>`**,
including tape items and feed cards — JavaScript-only click handlers are
invisible to crawlers and break middle-click and open-in-new-tab. And the
**footer is doing the heavy lifting**: it's the ugliest part of the page and the
main reason deep pages get discovered.

---

# 8. Error and edge pages

- **404** — search box + popular tickers + link to `/stocks`. People will hit
  `/stocks/WRONGTICKER` constantly. Never a bare "not found".
- **Delisted / acquired company** — not a 404. Serve the page with a banner
  ("acquired by X in 2023, data through final filing") and keep it indexed. The
  history is real and these pages get search traffic.
- **500** — static HTML with no DB dependency, or it fails when the DB does.
- **Maintenance** — static page the load balancer can serve during migrations.
- **Empty watchlist** — onboarding prompt with suggested stocks, not a blank
  table.

---

# 9. Page count and build order

Roughly **45 distinct templates**, most of them small.

**Ship with the MVP:** `/`, `/screener`, `/stocks`, `/stocks/{t}` + tabs,
`/screens`, `/screens/{slug}`, `/lists/{slug}`, `/methodology`, `/about`,
`/terms`, `/privacy`, `/disclaimer`, `/404`, `/500`, `robots.txt`,
`/sitemap*.xml`, `/healthz`.

**Second wave:** `/markets`, `/mood`, `/sectors/*`, `/news`, `/feed.rss`,
`/learn/*`, `/watchlist`, `/search`, `/compare/*`.

**Only when monetizing:** auth flows, `/account/*`, `/pricing`, checkout,
`/backtest`.

The legal trio and the error pages are in wave one deliberately. They take an
afternoon and they're the things that block a launch if left to the end.
