# Page designs — everything except landing and company

Landing is in `DESIGN-SPEC.md` §3.1 and §7. Company page is §3.2. This covers
the rest of the site.

Ordered by how much they matter, not by nav order.

---

# 1. `/screener` — the core tool

The page that has to be better than Finviz's, or there's no reason to switch.

```
┌─────────────────────────────────────────────────────────────────┐
│ ┌─────────────────────────────────────┐ ┌──────────┐ ┌───────┐ │
│ │ roe > 15 and pe < 25                │ │ Today ▾  │ │  Run  │ │
│ └─────────────────────────────────────┘ └──────────┘ └───────┘ │
│ + Add filter    Quality · Value · Growth · Dividend      ⌄ More │
├─────────────────────────────────────────────────────────────────┤
│ 214 matches · 2.1T total mkt cap    Columns ▾ Density ▾ ⬇ ★     │
├─────────────────────────────────────────────────────────────────┤
│ Ticker│ Company        │ Mkt cap│ P/E │ ROE  │ 3y rev │  5y     │
│ AAPL  │ Apple Inc.     │  3.52T │28.4 │147%  │  8.2%  │ ▁▃▄▆█   │
│ MSFT  │ Microsoft      │  3.11T │34.1 │ 35%  │ 14.1%  │ ▂▃▅▆█   │
└─────────────────────────────────────────────────────────────────┘
```

**Top bar, not a left rail.** Finviz uses a dense top grid, stockanalysis uses
a top bar. A left filter rail is the pattern from e-commerce and it's wrong
here — it permanently costs 240px of the horizontal space the table needs
most. Advanced filters live in a collapsible panel under the bar.

**Dual-mode input.** The text DSL stays the source of truth; the chip builder
writes into the same field. One representation, so they can never disagree.
Beginners click, power users type, and a typed query is what gets shared.

**Column presets before a custom picker.** Valuation / Quality / Growth /
Financials, each ~8 columns. Never show 30 by default — a wall of numbers with
no hierarchy is how screeners feel hostile.

**Summary line above the table.** Match count, aggregate market cap, median
P/E. Turns "here are 214 rows" into "here's what this slice of the market
looks like", and it costs one extra query.

**The vintage selector sits next to Run, not buried in settings.** It's the
differentiator; it should be the second thing on the page.

Gated actions (export, save, alert, arbitrary vintages) render visibly with a
lock and one click to upgrade. Hiding them converts worse than showing them.

---

# 2. `/backtest` — the page that justifies the subscription

Nothing else here is hard to copy. This is.

```
┌─────────────────────────────────────────────────────────────────┐
│ Screen  [ roe > 20 and pe < 15            ]                     │
│ From [2015-01-01]  To [2026-01-01]  Rebalance [Quarterly ▾]     │
│ Weighting [Equal ▾]                              [ Run backtest ]│
├─────────────────────────────────────────────────────────────────┤
│  CAGR      Max DD    Hit rate   vs S&P 500   Turnover           │
│  14.2%     -31.4%      58%       +3.1%/yr      42%/yr           │
├─────────────────────────────────────────────────────────────────┤
│        ╱╲                                        ╱╲             │
│      ╱╱  ╲    ╱╲              screen  ────     ╱╱  ╲            │
│    ╱╱      ╲╱╱  ╲╱╲    ╱╲╱╲                  ╱╱                 │
│  ──                  ╲╱      ╲╱╲╱╲╱  S&P ┄┄┄┄                   │
│  2015    2017    2019    2021    2023    2025                   │
├─────────────────────────────────────────────────────────────────┤
│ Holdings at 2019-03-31  (42 companies)          ← click a point │
│ AAPL · JNJ · MMM · TXN …        Open screen as of this date →   │
└─────────────────────────────────────────────────────────────────┘
```

**The interaction that ties the whole product together:** click any point on
the equity curve → see the holdings the screen returned at that rebalance →
click a holding → its company page *as of that date*, with the financials
anyone could actually see then. Screener, point-in-time engine and company page
become one coherent thing. Build this interaction even if you cut other
features to afford it.

**Be conspicuously honest about the caveats.** No transaction costs, no
slippage, no taxes, no dividends unless you model them, and delisted companies
handled explicitly. Put this above the results, not in a footnote. Overstated
backtests are both a credibility risk and a regulatory one — and being the site
that *doesn't* overstate is itself a positioning advantage.

**Cache aggressively.** Same screen + same parameters = same answer forever,
since the vintages are immutable. Key on a hash of the inputs.

---

# 3. `/compare/{a}-vs-{b}` — cheap to build, strong SEO

Two to four tickers side by side. Metric rows with the best value in each row
subtly highlighted. A normalized price chart (all series indexed to 100 at the
start) so different price levels are comparable.

High commercial intent, low competition, and it's one query plus a template.
Generate an intro paragraph from the actual numbers so the pages aren't thin
duplicates. Add "compare with…" links on every company page to seed them.

---

# 4. `/watchlist` — the retention surface

The page a returning user opens first, so it should answer "what happened to my
stocks?" not "here is a table."

- Their stocks with price, change, and the metrics they chose
- **Filing news filtered to just their companies** — this is the reason to come
  back daily, and you get it free from the news pipeline
- Aggregate stats: total value if quantities are entered, weighted P/E, sector
  mix
- Feeds the ticker tape (R5.6) and the alerts system

Works anonymously via `localStorage` and migrates on signup (R5.7). Let people
build one before asking for anything — they've then invested effort, and
signing up preserves it. That's a better funnel than gating it.

---

# 5. `/screens` and `/screens/{slug}` — curated screens

The index is a gallery of 15–20 named screens with a live match count and a
one-line description. Each detail page runs the screen server-side and renders
real results, with an "open in screener" button to edit it.

Two jobs: onboarding for people who don't know what to type, and indexable
landing pages for searches like `quality growth stock screen`.

---

# 6. `/lists/{metric}` — programmatic SEO

`highest-roe-stocks`, `lowest-pe-stocks`, `highest-dividend-yield`,
`best-fcf-yield`, and so on. Top 50 by one metric, regenerated nightly.

Each needs a genuinely generated intro paragraph from the data — thin
templated pages get filtered out. Roughly 30-50 of these, each one query
against `snapshot`. Cheap to build, and collectively they're a meaningful share
of the long-tail traffic.

---

# 7. `/methodology` — the trust page

Where the data comes from, how normalization works, how the mood index is
computed, and **what the known limitations are**.

That last section is the one that earns trust: 13F lags 45 days, XBRL coverage
degrades before 2011, banks and insurers don't have comparable margins, EPS
figures are as-reported and not adjusted. Every competitor hides this. Saying
it plainly is both differentiating and the kind of page that attracts links.

---

# 8. `/account` and `/pricing`

**Account:** saved screens, watchlists, alerts, plan and billing, API keys,
appearance (theme, density, **reduce motion** — which is where the ticker
preference from R5.4 lives).

**Pricing:** two columns, free vs pro, everything visible. No "contact us".
Annual toggle at roughly two months off. FAQ underneath handling cancellation,
data sources, and what happens to saved screens if you downgrade.

---

# 9. Site-wide

- **Header** on every page: logo, nav, compact search (`⌘K`), theme toggle,
  account. The market strip appears only on the landing page — it's context
  there and noise everywhere else.
- **404** should be useful: search box plus popular tickers. People will hit
  `/stocks/WRONGTICKER` constantly from bad links.
- **Footer**: real internal links to top tickers, all screens, all lists, plus
  methodology, pricing, terms, and the "not investment advice" line.

---

# 10. Build priority

If time is limited, this is the order that matters:

1. **Screener** — the product
2. **Watchlist** — cheap, and it's what makes people return
3. **Lists** — cheapest traffic per hour of work
4. **Screens** — onboarding plus traffic
5. **Backtest** — the revenue case, but only once there are users to sell to
6. **Compare** — nice, not urgent
7. **Methodology** — write it when the data settles
8. **Account / pricing** — only when there's something to charge for

Note that backtest sits fifth despite being the differentiator. Build the
traffic surfaces first — a subscription feature with no audience earns nothing.
The mitigation stays as agreed in `REQUIREMENTS.md`: keep one point-in-time
surface visible on every company page from day one so the differentiator is
never invisible while you wait.
