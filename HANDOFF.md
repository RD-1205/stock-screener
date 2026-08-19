# HANDOFF — read this first

You are picking up a US equity screener mid-build. This document is the
complete context: what it is, what works, what's blocked, what's queued, and
the non-obvious domain knowledge that will otherwise cost you days.

**Owner:** Rudra Arora · **Path:** `~/OneDrive/Desktop/projects/us-screener`
**Status as of this handoff:** 83/83 tests passing, ~7,300 lines, 21 routes,
runs locally, not deployed, seeded with 6 synthetic companies.

---

# 1. What this is

A stock screener and research portal for US equities, built entirely on free
SEC EDGAR XBRL filings.

**The differentiator, and the reason the project exists: point-in-time
screening.** Every free screener rebuilds history from today's data, so a
backtest sees figures that were later restated and companies that hadn't
listed yet. This one keeps the *filing date* on every datapoint, so any screen
can be replayed against a past date using only what was actually on file then.
That cannot be retrofitted — it requires the filing date from day one — and it
is already implemented and tested.

**Positioning.** Do not compete as "another free ratio screener with a nicer
UI" — Finviz, stockanalysis.com and Koyfin already own that and the UI edge
evaporates in a month. The layering is:

- News feed, search, company pages = **acquisition layer** (SEO traffic)
- Point-in-time + backtesting = **retention and revenue layer**

Full reasoning in `docs/PRODUCT-PLAN.md` §0.

---

# 2. Run it in 60 seconds

```bash
cd ~/OneDrive/Desktop/projects/us-screener
pip install -r requirements.txt
python -m screener.cli --db demo.db demo     # seeds 6 synthetic companies
python -m screener.cli --db demo.db serve    # http://127.0.0.1:8000
```

Tests (no network needed, ~15s):

```bash
for t in pipeline web foundation chart tape browse; do python tests/test_$t.py; done
```

Real data (this is the big blocker — see §8):

```bash
export SEC_USER_AGENT="Rudra Arora rudraarora120005@gmail.com"
python -m screener.cli init
python -m screener.cli tickers
curl -A "$SEC_USER_AGENT" -O https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
python -m screener.cli ingest --zip companyfacts.zip
python -m screener.cli prices --limit 500
python -m screener.cli normalize && python -m screener.cli snapshot
python -m screener.cli history --since 2018-01-01     # point-in-time vintages
```

Optional: `export FINNHUB_API_KEY=...` (free tier, finnhub.io/register) turns
the ticker belt and company header from "At close" to "Delayed 15 min".

---

# 3. Architecture

```
SEC EDGAR XBRL ──┐
                 ├─► facts ──► fundamentals ──► ratio engine ──┬─► snapshot
EOD prices ──────┘  (every    (canonical      (one code path)  └─► snapshot_history
                     version)  metrics)                            (vintages)
                                                                        │
                                              FastAPI + Jinja + HTMX ◄──┘
```

**Stack: Python monolith, server-rendered, islands for interactivity.**
FastAPI + Jinja2 + HTMX + Alpine, with three vanilla-JS islands (price chart,
ticker belt, search palette). **Do not migrate to Next.js/React** without
re-reading `docs/DESIGN-SPEC.md` §2.1 — organic search is the entire
acquisition plan and there are only three genuinely interactive surfaces on
the whole site. Islands ship ~25 KB on the landing page vs 200–400 KB for a
framework equivalent.

## Data model (`schema.sql`)

| Table | Holds | Key property |
|---|---|---|
| `companies` | CIK, ticker, name, SIC | |
| `facts` | Raw XBRL, **every version** | PK includes `accn`, so restatements coexist |
| `fundamentals` | Canonical metrics per period | `period_type` = FY / Q / INSTANT |
| `prices` | Daily OHLCV | |
| `snapshot` | Wide, one row per company | Rebuilt nightly; what the screener queries |
| `snapshot_history` | Same, per vintage date | Point-in-time |
| `index_members` | S&P 500 / Nasdaq 100 / Dow 30 | Needs a maintained CSV — S&P licenses the real list |
| `ingest_log` | Per-CIK crawl state | Lets a 10k-company crawl resume |

---

# 4. Domain knowledge you must not rediscover the hard way

These cost real debugging time. Every one is implemented and test-covered.

### 4.1 Nobody files a Q4

US companies file three 10-Qs and one 10-K. **There is no Q4 income statement
anywhere in EDGAR.** "Sum the last four quarters" silently returns three
quarters of this year plus one from last year. Measured error: ~7% — small
enough that nobody notices, large enough to make every margin and P/E wrong.
Q4 is derived as FY minus the 9-month YTD (`transform.py`).

### 4.2 Every company tags revenue differently

XBRL standardizes format, not vocabulary. ~14,000 us-gaap tags and no rule
forcing consistency. Apple uses
`RevenueFromContractWithCustomerExcludingAssessedTax`, others use `Revenues`,
older filings use the deprecated `SalesRevenueNet`. Screening on one tag
returns NULL for much of the market. `concepts.py` is the mapping table —
**this file is the actual moat.** Note that screener.in doesn't build this;
they license it from C-MOTS.

### 4.3 Cash flow is filed year-to-date

A 10-Q's cash flow covers 6 or 9 months, not the quarter. Discrete quarters
come from differencing consecutive YTD periods that share a fiscal-year start.
**Never difference across a gap** — an earlier bug produced a 9-month figure
labelled as a quarter.

### 4.4 Point-in-time needs three cutoffs, not one

1. **Filings**: `filed <= as_of`, *not* `period_end <= as_of`. A FY2023 10-K
   is filed in Feb 2024.
2. **Prices**: last close on or before the date.
3. **Universe**: only companies that had filed by then.

Get any one wrong and lookahead bias returns.

### 4.5 companyfacts strips dimensions

The `companyfacts` API returns only **consolidated, undimensioned** facts.
Segment revenue exists in XBRL but lives in dimensional facts qualified by
`StatementBusinessSegmentsAxis`. It is not in the response at any depth.
Segments need a second ingestion path — the Financial Statement *and Notes*
datasets. This blocks R3.11.

### 4.6 Nulls are load-bearing

A dash means "cannot be computed honestly", not zero. ROE and P/B are null on
negative equity; P/E on negative earnings; gross margin where no cost of
revenue was tagged. A test asserts an insolvent fixture company cannot appear
in a "highest ROE" screen. **Do not "fix" dashes by defaulting to 0.**

### 4.7 Prices: delayed, never real-time

Real-time US quotes need exchange agreements plus per-user fees. 15-min
delayed has no per-user exchange fee. `quotes.py` labels every response
(`"At close"` / `"Delayed 15 min"`) so the UI structurally cannot present
delayed data as live. That labelling is a licensing requirement, not polish.

### 4.8 Two traps already hit

- **`row.values` in Jinja** resolves to `dict.values` the *method*. Every
  company page 500'd. The key is now `vals`.
- **SIC 3674** matched both Electronics (3661–3699) and Semiconductors (3674);
  first-match-wins filed every chipmaker under Electronics. `sector_of()` now
  picks the **narrowest** matching range.

---

# 5. File map

```
schema.sql                 7 tables, SQLite now, Postgres-portable by design
screener/
  concepts.py              us-gaap tag → canonical metric   ← THE MOAT
  edgar.py                 SEC client: rate limit, User-Agent, parser
  ingest.py                live API / bulk zip / local dir loaders
  transform.py             restatements, Q4, YTD differencing, ratios, vintages
  analysis.py              per-period statement + ratio tables, YoY/QoQ, peers
  browse.py                SIC→sector, size bands, index membership, activity
  series.py                chart series + downsampling
  quotes.py                Finnhub delayed / last-close adapter
  prices.py                EOD ingest — stooq (free) / EODHD (~$20/mo)
  screen.py                query DSL → parameterized SQL (allowlisted)
  cli.py                   init/tickers/ingest/prices/normalize/snapshot/
                           history/screen/company/serve/demo
web/
  app.py                   21 routes: pages, HTMX partials, JSON API
  content/legal.py         terms, privacy, disclaimer, methodology, about
  static/css/tokens.css    "ink & amber" — dark default, full light peer
  static/css/base.css      elements + components
  static/js/{app,chart,tape}.js
  templates/               base, header, footer, tape, states, fintable + pages
tests/
  make_fixtures.py         6 synthetic filers reproducing every quirk in §4
  test_pipeline.py  (18)   data correctness
  test_web.py       (11)   routes + rendering
  test_foundation.py(17)   theming, layout, SEO, machine routes
  test_chart.py     (12)   series, downsampling, company page structure
  test_tape.py      (12)   ticker belt
  test_browse.py    (13)   sectors, bands, indexes, filter composition
```

## The fixtures (`tests/make_fixtures.py`)

Six *fake* companies — MODT, LEGC, DEPR, BANQ, NEGE, SEPT — each engineered to
break something: three different revenue tags, a restatement filed a year
late, no Q4 anywhere, YTD-only cash flow, a bank with no cost of revenue, a
negative-equity company, a September fiscal year end. Deterministic, no
network. **The "only 6 companies" question comes up a lot: that is the test
harness, not the product.**

---

# 6. Design decisions already made (don't re-litigate without cause)

| Decision | Rationale |
|---|---|
| Server-rendered monolith, not SPA | Organic search is the whole acquisition plan; 3 interactive surfaces don't justify a framework |
| Ink & amber palette, dark default | Green/red are reserved for price direction, so the accent can't be either; amber is uncommon in a category that's all blue |
| IBM Plex Sans + Plex Mono | Warm (matches the palette), one family for sans/mono/serif across 45 templates |
| Ticker belt: no pause button | Dropped at Rudra's request. WCAG 2.2.2 met via hover pause + focus pause + `prefers-reduced-motion` + in-app toggle |
| Belt is not sticky | Header is already 56px; 90px of permanent chrome is too much for a decorative strip |
| Mood gauge: 5 zones of 20 | 6 flips on noise; 4 has no true neutral and over-claims mid-range readings |
| SIC for sectors, not GICS | GICS is licensed by S&P/MSCI; SIC is free on every filing |
| News from 8-K filings, not a news API | No free commercial news API (Benzinga $166/mo). An 8-K *is* a material event, it's free, structured, and more differentiated |
| SQLite before Postgres | Zero ops; schema written portable. Migrate when the nightly write blocks reads |

---

# 7. Backlog

Two files, both current:

- **`docs/REQUIREMENTS.md`** — R1–R6, the feature backlog with checkboxes.
  Done: R3.1–R3.3 (price chart), R5.2–R5.4, R5.8–R5.9 (ticker belt),
  R3.8 partially (annual/quarterly toggle, YoY, ratio history, peers).
  Blocked: R3.7 shareholding, R3.11 segments (both need new pipelines).
- **`docs/PENDING-CHANGES.md`** — P1–P4, queued edits Rudra asked for.
  **He explicitly asked that these be batched, not applied one at a time.**
  Do not implement them individually unless he says go.

Other docs: `PRODUCT-PLAN.md` (positioning, tiers, monetization, legal),
`DESIGN-SPEC.md` (frontend engineering, data sourcing costs, performance
budget), `PAGES.md` (page-by-page design), `SITEMAP.md` (45 routes,
indexation rules, crawl traps), `ROADMAP.md` (original build plan).

---

# 8. What is blocked, and on what

### The one that matters: real data has never been ingested

Everything runs on 6 synthetic companies. Consequences:

- The `concepts.py` tag map has only met 6 filers — **real-world coverage is
  unknown**. If `operating_cash_flow` resolves at 40% on real filings, the
  financials tables are mostly dashes and the screener filters on data that
  isn't there.
- Every network path (`fetch_tickers`, `fetch_companyfacts`, both price
  providers) is written and unit-tested but **has never run against a live
  endpoint**. Expect small fixes on first contact.
- Only 2 of 5 market-cap bands appear, because all 6 fixtures sit between $7B
  and $81B.
- Ratio columns look identical across years because fixtures use constant
  margins.

**First action for whoever picks this up: get Rudra to run the ingest, then
open `/coverage`.** That page ranks every metric by resolution rate and is the
work queue for `concepts.py`. It is worth more than any feature currently in
the backlog.

### Also blocked

| Item | Blocked on |
|---|---|
| Shareholding (R3.7) | New pipeline: 13F datasets + Forms 3/4/5 XML. Free, ~2 weeks. `edgartools` (MIT) would save most of it |
| Revenue segments (R3.11) | Financial Statement *and Notes* datasets — see §4.5. ~1.5–2 weeks, shares plumbing with R3.7 |
| Live-ish prices | `FINNHUB_API_KEY` (free, 60 req/min). Adapter is written; no code change needed |
| S&P 500 membership | No free authoritative list. Needs a maintained CSV in the repo |
| Deployment | Postgres, cache layer, background jobs, observability. ~2–3 weeks. `PRODUCT-PLAN.md` §2.2 |

---

# 9. Conventions

- **Tests are the contract.** 83 currently pass. Several encode domain rules
  that look like nitpicks and are not — e.g. sitemap and robots.txt must not
  contradict each other; `chart.js` must not load on non-company pages.
- **Comments explain *why*, never *what*.** The existing code comments the
  reasoning behind non-obvious choices. Match that.
- **Verify, don't assume.** Run the app and check real output. Several bugs
  here were found only by looking at rendered pages, not by reading code.
- **Be honest about limits.** The product's positioning is "we show our work" —
  the methodology page lists known limitations, nulls are explained rather than
  hidden, and delayed data is labelled. Don't paper over gaps with plausible
  placeholders.
- **No fake data in the UI.** The landing page deliberately shows only real
  data rather than stubbing the news feed and mood gauge.
- **Rudra's working style:** he asks direct questions, wants reasoning not
  just output, pushes back on over-engineering, and prefers being told when
  something can't be built over being given a mock. He is currently batching
  UI changes — collect them in `PENDING-CHANGES.md`, don't apply piecemeal.

---

# 10. If you do one thing

Get the real ingest run and read `/coverage`. Everything else in this backlog
is speculation until you know what the data actually supports.
