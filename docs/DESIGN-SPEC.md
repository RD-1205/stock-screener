# Frontend engineering spec

How to build the pages in `REQUIREMENTS.md` to a high standard. Written as an
engineer's proposal: every recommendation has a reason, and the expensive or
risky parts are called out rather than buried.

---

# 1. What changed, and what that costs

The requirements move this from *a screener* to *a stock research portal*. That
is a legitimate direction, but be clear-eyed: it roughly **doubles the data
pipeline**. Today the pipeline ingests one thing (XBRL financials). The new
pages need four more:

| Section | New data required | Free? | Effort |
|---|---|---|---|
| Price chart | Daily OHLCV, 10y+ | ~$20/mo | Low |
| News feed | Filing events + headlines | Partly | **High** |
| Shareholding | 13F, 13D/G, Forms 3/4/5 | **Yes** | Medium-high |
| Search | Ticker/name index | Yes | Low |

Two of these deserve a decision before any UI work.

## 1.1 News — the only genuinely expensive requirement

There is no free, commercially-licensed, comprehensive stock news API. The
market:

| Source | Cost | Verdict |
|---|---|---|
| **SEC 8-K + filing events** | Free | **Build on this** |
| Finnhub | Free tier (60 req/min), paid from ~$50/mo | Good augmentation |
| Marketaux | Free tier ~100/day, ~$30/mo | Workable |
| Benzinga | ~$166/mo | Too expensive at this stage |
| NewsAPI | $449/mo for commercial | No |

**Update 2026-09 (built, live on the homepage):** the actual ask turned out to
be broader than filings — "geopolitical news / tech news and everything that
revolves around the stock and moves them," e.g. a 10-year Treasury yield
story, not a company's own 8-K. **The feed is general-market-news-first**,
via Finnhub's free tier (`screener/news.py`, same key as `quotes.py`):
headline, source, timestamp, link out to the original article — never
republish the body, per Finnhub's terms and the site's own house rule.
Verified live against the real key: `/news?category=general` returns exactly
this kind of story (sanctions, shipping-lane conflict, rate-sensitive
business news), not company press releases.

Filing events (8-K decoded to plain English, Form 4, 13D/G, earnings
arrivals) are still a real differentiator and still worth building — just as
a **tagged category on top of the general feed**, not the primary content, and
not yet built. Original filings-first reasoning kept below for that
follow-up:

An 8-K is, by definition, a material event the company was legally required to
disclose — an acquisition, an executive departure, a restructuring, a material
agreement. That *is* news, it is free, it is structured, it arrives before most
journalism about it, and it fits a product whose entire premise is "we read the
filings."

A filings-tagging pass would add:

- **8-K filings**, with the item number decoded into plain English
  (Item 5.02 → "Executive departure or appointment")
- **Earnings** — 10-Q/10-K arrivals, with the headline numbers pulled straight
  from XBRL and compared to the prior period
- **Form 4** — notable insider buys and sells
- **SC 13D/G** — someone crossed 5% ownership

Layered onto the general feed as filters/tags, this is more differentiated
than a generic news aggregator competitors run, without giving up "genuinely
market-moving news" as the homepage's actual lead content.

**Licensing rule, non-negotiable:** for third-party news, display headline +
source + timestamp + link only. Never republish article bodies. Most providers'
terms require this and the ones that don't still expect it.

## 1.2 Shareholding — free, rich, and underexploited

This is the pleasant surprise. The US equivalents of screener.in's shareholding
section are all free on EDGAR and mostly XML:

| Form | What it gives | Cadence |
|---|---|---|
| **13F-HR** | Institutional holdings (>$100M AUM managers) | Quarterly, XML |
| **SC 13D / 13G** | Anyone crossing 5% ownership | On event |
| **Forms 3/4/5** | Insider transactions | Within 2 business days, XML |
| **DEF 14A** | Officer/director ownership, comp | Annual, HTML (harder) |

The SEC also publishes [Form 13F structured data
sets](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets) as
flattened quarterly files — same trick as the XBRL bulk zip, one download
instead of thousands of requests.

`edgartools` (MIT-licensed, no API key) already parses Forms 3/4/5 and 13F and
would save weeks. Worth using here even though the core pipeline is
dependency-free — this is a different problem with a good existing solution.

**Present the caveats honestly**, because they're significant and most sites
hide them: 13F is filed 45 days after quarter end so it is always stale; it
only covers managers above $100M AUM; and it only shows long US equity
positions, so shorts, bonds and derivatives are invisible. A shareholding page
that says this plainly is more trustworthy than one that doesn't.

---

# 2. Architecture: server-rendered shell, hydrated islands

## 2.1 Revisiting the stack call

Earlier I argued for keeping the Python monolith because organic search is the
acquisition channel. The new requirements are more interactive, so it's fair to
re-ask the question. My answer doesn't change, but the reasoning gets sharper.

Count the genuinely interactive surfaces on the whole site:

1. The price chart
2. The command-palette search
3. The financial statement table

That's three. Adopting a full client framework means hydrating every page to
get three widgets. **Islands are strictly less JavaScript for exactly the same
result**, and they preserve server rendering on the pages that earn traffic.

The thing that has actually changed since the "SPA vs SSR" framing was settled:
**the View Transitions API is now usable**. You can get app-like animated
navigation on a plain server-rendered site in about ten lines of CSS. The main
historical reason to reach for a SPA — "server-rendered sites feel clunky when
you navigate" — is largely gone.

```
Proposed:
  FastAPI + Jinja2      every page, server-rendered
  HTMX                  server round-trips (feed pagination, tab loads, screens)
  Alpine.js  ~15 KB     local UI state (tabs, dropdowns, toggles, modals)
  View Transitions      cross-page animation, native, no library
  ── islands, loaded only where used ──
  Lightweight Charts    ~45 KB   price chart
  Search palette        ~4 KB    hand-written, no dependency
  Table interactions    ~6 KB    hand-written, on company + screener only
```

Total JS on the landing page: **~25 KB**. On a company page: **~70 KB**. A
typical Next.js equivalent ships 200–400 KB before your own code.

**When I would change my mind:** if you add real-time streaming quotes across
many symbols, drag-to-reorder dashboards, or multi-pane layouts with
independent state. None of those are in the requirements.

## 2.2 Why not Astro

Astro is the best-in-class islands framework and would be a reasonable choice.
It costs you a second runtime and a JS build toolchain next to a Python data
layer, and buys you an ergonomics improvement over Jinja + Alpine — not a
capability you lack. Not worth it for a solo build. Revisit if a second
developer joins who prefers JS.

---

# 3. Page designs

## 3.1 Landing page

```
┌──────────────────────────────────────────────────────────────┐
│  logo        Screener  Stocks  Screens          ⌘K   Sign in │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│            Search any US company or ticker                   │
│   ┌────────────────────────────────────────────────────┐    │
│   │ 🔍  Apple, AAPL, semiconductors…                    │    │  ← 64px tall
│   └────────────────────────────────────────────────────┘    │
│   AAPL · MSFT · NVDA · TSLA        5,000 companies · 15y     │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  S&P 500 ▲0.4%   Nasdaq ▲0.8%   ↑312 ↓188   Vol 4.2B        │  ← market strip
├──────────────────────────────────────────────────────────────┤
│  Latest        [All ▾] [Sector ▾] [Mkt cap ▾] [Watchlist]    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐     │
│  │ 8-K  ·  2h ago                                      │     │
│  │ NVDA  Nvidia — Executive appointment                │     │
│  │ Item 5.02 · new Chief Financial Officer named       │     │
│  │ NVDA ▲1.2%              Read filing →               │     │
│  └────────────────────────────────────────────────────┘     │
│  ┌────────────────────────────────────────────────────┐     │
│  │ EARNINGS  ·  4h ago                                 │     │
│  │ AAPL  Apple — Q3 FY26 results filed                 │     │
│  │ Revenue $94.2B ▲8.1% y/y · EPS $1.64 ▲12%          │     │
│  │ ▁▂▄▆█ 8 quarters      Full financials →             │     │
│  └────────────────────────────────────────────────────┘     │
│                          ⋮  (infinite scroll via HTMX)       │
└──────────────────────────────────────────────────────────────┘
```

Design decisions worth defending:

**Search above the fold, above the news.** Requirement R2 says beautiful and
big; more importantly, a returning user's intent is almost always "look up a
company." Put the highest-intent action first and let the feed reward the
undirected visitor below it.

**The feed is the SEO body.** Each card is a real internal link to
`/stocks/{ticker}`. A landing page that changes daily and links out to 5,000
company pages is a genuinely good crawl surface.

**Cards carry data, not just headlines.** The earnings card showing revenue,
change, and a sparkline is what makes this yours rather than an RSS reader. You
have the numbers already — use them.

## 3.2 Company page

```
┌──────────────────────────────────────────────────────────────┐
│ AAPL  Apple Inc.        $232.14  ▲1.24 (0.54%)   [★ Watch]   │  ← sticky
│ Technology · Consumer Electronics        As of [Today ▾]     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│      ╱╲    ╱╲                                                │
│    ╱╲  ╲╱╲╱  ╲    ╱╲╱╲                                       │  ← chart, first
│  ╱╱      ╲    ╲╱╲╱    ╲╱╲                                    │
│                                                              │
│  1M  6M  1Y  5Y  10Y  MAX      + Compare    ⚑ Events   ⚙     │
├──────────────────────────────────────────────────────────────┤
│  Overview  News  Financials  Shareholding  Filings  Peers    │  ← sticky subnav
├──────────────────────────────────────────────────────────────┤
│  Mkt cap 3.52T   P/E 28.4   ROE 147%   Rev CAGR 8.2%  …      │
│                                                              │
│  [ section content, loaded per tab ]                         │
└──────────────────────────────────────────────────────────────┘
```

**Tabs load via HTMX on demand, and each has its own URL**
(`/stocks/AAPL/financials`). Server-rendered, indexable, shareable,
back-button-correct — and the initial page payload stays small because you
aren't shipping four tabs of data to render one.

### Chart (R3.1–R3.5)

**TradingView Lightweight Charts.** 45 KB, purpose-built for financial series,
canvas-rendered so it handles 10 years of daily bars without stutter. The
alternatives are worse here: Chart.js is general-purpose and slower at this
density; D3 means building crosshair, tooltips and range selection yourself.

- Data from a dedicated `/api/chart/{ticker}?range=5y` endpoint returning
  arrays, not objects — `[[ts, close], …]` is roughly 60% smaller than
  `[{date, close}, …]` over a few thousand points.
- Downsample server-side by range: daily for ≤1Y, weekly for 5Y, monthly for
  MAX. Never ship 2,500 points to draw 600 pixels.
- Lazy-load the library via `IntersectionObserver` so the landing page and
  non-chart tabs never pay for it.
- Log-scale toggle on ranges over 5Y — mandatory for anything that compounded.
- **Event markers** (R3.5) are the differentiator: 8-K, earnings, and insider
  transactions plotted on the price line. Nobody free does this well, and you
  already have the filing dates.

### Financials (R3.8)

- Annual / quarterly toggle — Alpine state, both datasets sent once, no refetch
- Rows expand to show component line items
- **Click any row to chart it** — reuses the same chart island, no new code
- Hover any figure → provenance popover: us-gaap tag, form, filing date, EDGAR
  link
- Sticky first column and sticky header; horizontal scroll on mobile
- Restated values flagged with a marker linking to the restatement timeline

### Shareholding (R3.7)

Four blocks: institutional holders (13F, sortable, change vs prior quarter),
>5% holders (13D/G), insider transactions (Form 4, last 12 months), and an
ownership-mix-over-time stacked area chart. Staleness banner at the top of the
section stating the as-of date and the 45-day lag.

---

# 4. What "high end" actually means in engineering terms

Polish is not a visual layer applied at the end. It's a set of specific
technical behaviours.

## 4.1 Perceived performance beats measured performance

- **Skeletons that match the final layout exactly.** A skeleton with different
  dimensions than the content it replaces causes layout shift, which reads as
  cheap. Measure CLS and keep it near zero.
- **Prefetch on intent.** Preload a company page on link hover or touchstart —
  HTMX has `hx-trigger="mouseenter"` with `preload`. A ~150 ms head start makes
  navigation feel instant.
- **Stream the shell.** FastAPI supports `StreamingResponse`. Send header,
  search and chart container immediately; stream the slower sections after.
  First paint stops waiting on the slowest query.
- **Optimistic UI on toggles.** Annual/quarterly should flip immediately, not
  after a round-trip.

## 4.2 The search palette (R2)

The highest-craft component on the site. Requirements:

- Debounce 120 ms, and **abort in-flight requests** on the next keystroke —
  otherwise slow responses arrive out of order and results flicker backwards.
- Server target < 20 ms. At 5,000 companies this is trivial: an in-memory
  index rebuilt nightly, or SQLite FTS5 / Postgres `pg_trgm`. **Do not reach
  for Elasticsearch** — it's the wrong tool by three orders of magnitude here.
- Rank deterministically: exact ticker → ticker prefix → name prefix → name
  substring → fuzzy, tie-broken by market cap. Users expect `AA` to surface
  Alcoa, not Applied Materials.
- Full keyboard: `⌘K`/`Ctrl+K` anywhere, arrows, enter, escape, focus trap,
  focus restored on close.
- `aria-live` region announcing result counts.
- Empty state → recent searches (localStorage) → trending.

## 4.3 Motion

- 150–250 ms, ease-out. Longer reads as sluggish.
- Only on state change. Nothing loops, nothing animates on load.
- `@media (prefers-reduced-motion: reduce)` disables all of it. Not optional.
- View Transitions for page navigation, with the sticky header marked
  `view-transition-name` so it persists across pages.

## 4.4 Numbers — finance-specific craft

- `font-variant-numeric: tabular-nums` everywhere. Without it, columns jitter
  and the whole table reads as untrustworthy.
- Fixed precision rules per metric type, applied centrally — never per-template.
- Colour encodes direction only. Never a categorical rainbow.
- Nulls render as `—` with a tooltip explaining *why* (no cost of revenue
  tagged; negative equity makes this undefined). You already return null
  correctly in the data layer; the UI should explain the decision rather than
  hide it. This is a trust feature, not an edge case.

## 4.5 States

Every async surface needs four designed states: loading, empty, error, and
partial. The one people skip is **partial** — a company with 3 years of history
instead of 10, or no 13F holders. Design those explicitly or they'll render as
broken.

## 4.6 Performance budget

| Metric | Target | Enforcement |
|---|---|---|
| LCP | < 1.5 s | Server-rendered, cached, no hero image |
| INP | < 200 ms | Islands only, no full-page hydration |
| CLS | < 0.05 | Reserve dimensions for chart and images |
| JS, landing | < 30 KB gz | Alpine + HTMX + palette |
| JS, company | < 90 KB gz | + Lightweight Charts |
| TTFB | < 200 ms | Cache by content hash, expire on nightly build |

Wire Lighthouse CI into the deploy so regressions fail the build rather than
being discovered months later.

## 4.7 Accessibility

Not a compliance checkbox — it's most of what makes a keyboard-heavy data tool
feel professional. Semantic `<table>` with `<caption>` and `<th scope>`. Focus
visible everywhere. Full keyboard reachability. Contrast ≥ 4.5:1 including in
dark mode, where muted grays are the usual failure. Screen-reader announcement
when result counts change.

---

# 5. Caching strategy

Everything on this site changes at most daily, which makes caching easy and
high-leverage.

| Surface | TTL | Key |
|---|---|---|
| Search index | Nightly rebuild | In-memory |
| Company page | Until next ingest | `ticker + data_version`, ETag |
| Chart series | Immutable | `ticker + range + last_trading_day` |
| News feed | 60 s | `filters` |
| Screen results | Until next ingest | `hash(query, vintage, sort, page)` |
| Static assets | 1 year | Content hash in filename |

Immutable chart URLs matter: because the key includes the last trading day, a
new day produces a new URL and the old one can be cached forever by the browser
and CDN.

---

# 6. Build sequence

Ordered so each step ships something usable and nothing blocks on the step
after it.

**Step 1 — Foundations (1 week).** Extract the design tokens out of
`base.html` into a documented stylesheet. Add Alpine. Build the layout shell,
sticky header, dark/light, and the four page states as reusable partials.

**Step 2 — Search (1 week).** R2 end to end. Index, endpoint, palette,
keyboard, `⌘K`. Ship it on the current site — it's independently useful.

**Step 3 — Chart (1 week).** R3.1–R3.4. Chart endpoint with downsampling,
Lightweight Charts island, range selector, comparison overlay.

**Step 4 — Company page rebuild (1.5 weeks).** Tabbed layout, HTMX tab
loading, interactive financials with provenance popovers.

**Step 5 — Shareholding pipeline (2 weeks).** The biggest new data lift: 13F,
13D/G, Forms 3/4/5 ingestion, schema, then the section UI.

**Step 6 — News pipeline + landing page (2 weeks).** 8-K decoding, earnings
event generation, optional Finnhub augmentation, feed UI, filters, market
strip.

**Step 7 — Polish (1 week).** View transitions, prefetching, streaming,
skeletons, Lighthouse CI, accessibility audit.

Roughly **9–10 weeks** of steady part-time work. Steps 2 and 3 are the highest
visible-quality-per-hour and can ship against the existing site immediately.

---

# 7. Market pulse band — ticker tape and sentiment gauge

## 7.1 "Live" prices: the decision behind R5

This is the requirement with a licensing cost attached, so decide it before
designing the component.

| Option | Exchange fees | Verdict |
|---|---|---|
| **Real-time consolidated** | UTP/CTA display fees (~$250/mo base + admin) **plus per-user non-professional fees** | Not viable at zero revenue |
| **15-minute delayed** | **No per-user exchange fees** | **This is the answer** |
| IEX-only real-time | Cheap | IEX is ~2% of volume; prices look wrong vs everywhere else |
| End-of-day | Free-ish | Fine for fundamentals, dead for a ticker tape |

The 15-minute delay exists because exchanges monetize real-time feeds; delayed
data is deliberately made cheap to avoid antitrust exposure. Every free
financial portal you've used runs on it.

**Recommendation:** 15-minute delayed intraday, from Finnhub's free tier during
development and a paid tier (~$50/mo) once you have users. Label it plainly —
`Delayed 15 min` next to the tape. Presenting delayed data as live is both a
trust problem and a licensing violation, and the label costs you nothing
because everyone expects it.

This changes only the labelling, not the design. Build the tape so the data
source is swappable — if you later license real-time, you change one adapter
and one label.

## 7.2 Ticker tape engineering

Layout — indices fixed, tape scrolls in the remaining space:

```
┌──────────────────────────────┬────────────────────────────────────────┐
│ S&P 500 ▲0.4%  Nasdaq ▲0.8%  │  AAPL 232.14 ▲0.5% · MSFT 418.2 ▼0.3%…│
│ (fixed, never moves)         │  (scrolls)          [⏸] [Watchlist ▾] │
└──────────────────────────────┴────────────────────────────────────────┘
```

**Animate with CSS, not JavaScript.** Render the symbol list twice inside a
flex track and animate `transform: translateX(0 → -50%)` on a linear infinite
loop. Because the list is duplicated, the wrap is seamless. `transform` is
GPU-composited, so this costs no main-thread work — a JS-driven scroll would
burn frames and stutter under load.

```
duration = symbol_count × 3s     keeps speed constant as the list grows
animation-play-state: paused     on :hover, :focus-within, and .is-paused
```

Mark the duplicated copy `aria-hidden="true"` so screen readers don't read
every symbol twice.

**Refresh without restarting the animation (R5.8).** The naive approach —
re-render the tape HTML on each poll — resets the CSS animation and the tape
visibly jumps. Instead, poll a JSON endpoint every 30–60 s and mutate only the
price and change text nodes in place. The track keeps scrolling; the numbers
change underneath it. Flash a brief background tint on changed cells so the
update is noticeable without being distracting.

Polling, not SSE or WebSocket. With a 15-minute delay there is nothing to
stream, and polling is a fraction of the operational complexity.

**Watchlist for anonymous users (R5.7).** Store it in `localStorage` and let
people build a watchlist with no account at all. On signup, migrate it to the
server. This is a better conversion funnel than gating the feature — the user
has already invested effort and signing up preserves it.

**Accessibility is a hard requirement here.** WCAG 2.2.2 mandates a pause
mechanism for auto-moving content over five seconds. Ship a visible pause
button, honour `prefers-reduced-motion: reduce` by rendering a static
non-scrolling list, and keep every tape item keyboard-focusable with
`:focus-within` pausing the scroll.

## 7.3 Sentiment gauge — build it, don't borrow it

CNN's Fear & Greed Index has no public API, and scraping it would be against
their terms and legally exposed. The name is theirs too. But the methodology is
published, and **every input is available free** — several of them from data
you already have.

CNN weights seven indicators equally, each measured as a standard-deviation
move from its own recent norm:

| Component | Input | Source |
|---|---|---|
| Market momentum | S&P 500 vs its 125-day MA | **Your own price DB** |
| Stock price strength | 52-week highs vs lows | **Your own price DB** |
| Stock price breadth | Advancing vs declining volume | **Your own price DB** |
| Market volatility | VIX vs its 50-day MA | FRED, free |
| Junk bond demand | High-yield spread (`BAMLH0A0HYM2`) | FRED, free |
| Safe haven demand | Stock vs Treasury returns | FRED + your DB |
| Put/call ratio | CBOE equity put/call | CBOE, free daily |

Three of seven come straight from the price history you're already storing for
5,000 tickers — you are unusually well positioned to compute this. FRED's API
is free with a key and covers the rest.

**Name it something else.** "Market mood", "risk appetite" — anything that
isn't CNN's trademark.

**Publish the methodology and the component breakdown.** Every competitor shows
a needle and nothing else. Showing your inputs, their z-scores and their
contributions is consistent with the rest of the product's "show your work"
posture, and it's the sort of page that earns links.

### The feature that makes this more than decoration

You store point-in-time snapshots. That means you can compute the sentiment
index historically and then **join it to the screener**:

> Sentiment is at 22 (fearful). The last three times it was below 25 were
> Mar 2020, Sep 2022 and Oct 2023 — **run your screen as of those dates →**

No other free site can do this, because it requires filing-date-accurate
fundamentals *and* a sentiment series. It also solves the risk flagged in
`REQUIREMENTS.md`: the differentiator becomes visible on the landing page
instead of buried behind a paywall on an inner page.

Compute nightly, store one row per day in a `sentiment` table, and cache the
gauge for the trading day. It costs one small job and one tiny table.

## 7.4 Where it goes on the landing page

```
        [ big search ]
┌─────────────────────────────────────────────────┐
│ indices (fixed) │ ticker tape (scrolls) [⏸][▾] │   ← R5
├─────────────────────────────────────────────────┤
│  ◔ Mood 42       │ ↑312 ↓188  │ Movers: NVDA…  │   ← R6 + breadth
├─────────────────────────────────────────────────┤
│  Latest  [filters]                              │
│  … filing-driven news feed …                    │
```

One "market pulse" band holding the tape, the gauge and breadth, sitting
between the search and the news feed. It gives the returning visitor a
five-second market read before they scroll, without pushing the feed below the
fold on a laptop.

Added build cost: roughly **1 week** for the tape and **1 week** for the
sentiment gauge including the nightly job and methodology page. Slot both into
Step 6 alongside the landing page rebuild.

---

# 8. Decisions needed

1. **News budget.** Filings-only (free) or filings + Finnhub free tier or a
   paid feed? My recommendation is filings-first, Finnhub free tier as
   augmentation, revisit only if users ask.
2. **Shareholding depth.** All four form types, or start with 13F + Form 4
   (roughly 60% of the value for 40% of the work)?
3. **Real-time prices.** EOD is assumed throughout. Intraday means exchange
   licensing and real cost — confirm EOD is acceptable, because it changes the
   chart, the market strip, and the header price.
4. **Scope honesty.** This is ~10 weeks before the backtesting engine gets
   built. Confirm that trade is deliberate — the portal features are what get
   you found, but point-in-time is still the only thing here that competitors
   can't copy in a weekend.
