# Product plan — frontend, backend, and the path to users

Written as a design proposal, not a spec. Everything here is arguable; the
reasoning is included so you can disagree with the conclusion rather than the
vibe.

---

# 0. Positioning — decide this before designing anything

The free US screener market is crowded and the incumbents are good:

| Competitor | Model | Strength | Weakness you can exploit |
|---|---|---|---|
| Finviz | Free + $25/mo | Enormous traffic, fast | Dated UI, shallow fundamentals, no history |
| stockanalysis.com | Free | Beautiful, huge SEO footprint | No screening depth, no backtest |
| Koyfin | Freemium, $39+/mo | Charting, institutional feel | Expensive, heavy, not screening-first |
| Simply Wall St | Freemium | Visual, beginner-friendly | Opinionated snowflakes, weak for power users |
| Stockopedia | £30/mo, no free tier | StockRanks with history | UK-centric, paywalled, no free entry |

**Do not enter as "another free ratio screener with a nicer UI."** That's the
one position where you lose on day one — your UI advantage evaporates in a
month and you have no traffic.

**Enter as the screener that can prove a screen works.**

> Every free screener rebuilds history from today's data. Their backtests see
> restated figures nobody had at the time and companies that hadn't IPO'd yet.
> Ours doesn't, because we keep the filing date on every datapoint.

That claim is true, it's already implemented, it is genuinely hard to retrofit,
and it appeals to exactly the user who eventually pays. Everything below is
designed around making that claim legible in the first ten seconds.

**Target user:** the retail investor who has outgrown "show me low P/E stocks"
and has started asking "would this actually have worked?" They are already
paying for something, or actively frustrated that they can't.

---

# 1. Frontend

## 1.1 Stack — keep the Python monolith, and here's why

The instinct at this stage is "now make it a real Next.js app." I'd push back.

**Organic search is your entire acquisition channel.** You have no ad budget,
no audience, no distribution. Finviz and stockanalysis.com get the majority of
their traffic from people searching `AAPL stock`, `best dividend stocks`,
`low pe stocks`. That traffic lands on *content pages* — one per ticker, one
per screen. You will have ~5,000 ticker pages and a few hundred screen pages.
Those pages must be server-rendered, fast, and indexable, or the growth plan
doesn't exist.

FastAPI + Jinja already does that natively and you already have it working.
Rebuilding as a client-rendered SPA would actively destroy the growth channel.
Next.js with SSR would preserve it — but it buys you nothing you need while
costing a second runtime, a second deploy, API/client version skew, and CORS.

**Recommendation:**

| Layer | Choice | Rationale |
|---|---|---|
| HTML | FastAPI + Jinja2 (keep) | SSR by default, one deploy, SEO for free |
| Server interactions | HTMX (keep) | Run screen, sort, paginate, switch vintage |
| Local interactions | Alpine.js (add, ~15 KB) | Dropdowns, tabs, column picker, modals — no round-trip |
| Data grid | Tabulator (add, on the screener route only) | Virtualization, frozen columns, resize. Framework-free |
| Charts | uPlot (add, ~40 KB) | Fast, tiny. Chart.js if you want easier API |
| CSS | Hand-rolled tokens (keep) + a little utility CSS | You already have a coherent system; Tailwind is optional |

**When to revisit:** if the screener becomes genuinely app-like — drag-to-
reorder columns, multi-pane layouts, real-time updates — mount a React *island*
on `/screener` only. Keep every SEO page server-rendered. Do not rewrite the
whole site to fix one route.

The honest cost of this choice: it's a less fashionable portfolio artifact than
"Next.js + React". Counter-argument for interviews — "I chose server rendering
because organic search was the acquisition channel and a SPA would have killed
it" is a *better* signal than another Next.js CRUD app. It shows you optimized
for the business constraint.

## 1.2 Information architecture

```
/                             landing
/screener                     the tool (state lives in the URL)
/screens                      index of curated screens
/screens/{slug}               e.g. /screens/quality-compounders   [SEO]
/stocks/{ticker}              company page                        [SEO]
/stocks/{ticker}/financials   deep financials tab
/stocks/{ticker}/filings      filing history + restatements
/lists/{metric}               e.g. /lists/highest-roe-stocks      [SEO, programmatic]
/compare/{a}-vs-{b}           head to head                        [SEO, programmatic]
/backtest/{screen}            point-in-time replay of a screen    [the paid hook]
/methodology                  where the data comes from           [trust + SEO]
/pricing
/login  /signup  /account
/api/docs                     public API reference
```

Two notes on naming. Use `/stocks/AAPL`, not `/company/AAPL` — it matches how
people search and how competitors rank. And make the screener's full state live
in the query string, because a shareable screen URL is free distribution.

## 1.3 Landing page, section by section

Sections in order, with the job each one does:

**1. Hero — and it must be interactive**

Headline: *Screen the market as it actually was.*
Subhead: *A US stock screener built on SEC filings, with the filing dates kept.
Run any screen against today — or against 2019.*

Below the copy, a **live, working query box with results**. Not a screenshot,
not a video. Pre-filled with a good screen, results already rendered, no signup.

This is the highest-leverage element on the page. For a tool product, the
fastest path to belief is use. Every extra click before the user touches the
product costs you conversions, and a screener is cheap to demo because the
first query costs you one indexed database read.

**2. The differentiator, shown not told**

The vintage toggle, side by side. Same query, two dates, numbers visibly
change. One callout underneath:

> This company restated its Q1 revenue 18 months later. Screeners that rebuild
> history from today's data show you the corrected figure in 2019 — a number
> nobody had. We show you what was actually on file.

If a visitor understands only one thing on this page, make it this.

**3. Preset screens as cards**

Six to eight cards — "Quality compounders", "Deep value", "Cash machines",
"Fallen angels". Each links to `/screens/{slug}`, which is a real indexable
page with live results. These do double duty: onboarding for people who don't
know what to type, and SEO landing pages.

**4. Company page preview**

One large screenshot or live embed of a company page. Job: prove depth. The
screener gets them in; the company pages make them stay.

**5. Trust / methodology block**

> Every number comes from an SEC XBRL filing. Hover any figure to see the exact
> us-gaap tag it was resolved from, and click through to the filing on EDGAR.

This is a genuine competitive advantage and almost nobody advertises it.
Vendors buy opaque normalized feeds; you can show your work. Say so.

**6. Coverage stats strip**

`5,000+ companies · 15 years of filings · 30 metrics · updated nightly`
Concrete numbers, no adjectives.

**7. Pricing**

Two columns, free vs pro. Do not hide pricing — hiding it reads as enterprise
sales and this is a self-serve product.

**8. FAQ**

Six to ten questions. Handles objections ("where does the data come from?",
"how is this different from Finviz?", "is this investment advice?") and adds
indexable long-tail content.

**9. Footer with real internal links**

Top 50 tickers, all screens, all lists. Ugly, unglamorous, and it's how search
engines discover your programmatic pages.

## 1.4 The screener — the core UI

**Dual-mode query input.** The text DSL is the power feature and stays the
source of truth. Above it, a chip builder — `[Metric ▾] [> ▾] [value]` — that
writes into the same text field. Beginners use chips, power users type, and
they never disagree because there is one representation.

**Layout:**

```
┌────────────────────────────────────────────────────────┐
│ [ roe > 15 and pe < 25          ] [Today ▾] [Run]      │
│ + Add filter    Presets: Quality · Value · Growth      │
├────────────────────────────────────────────────────────┤
│ 214 matches   Columns ▾   Density ▾   Export ⬇  Save ★ │
├────────────────────────────────────────────────────────┤
│ Ticker │ Company      │ Mkt cap │ P/E │ ROE │ ...      │  ← sticky
│ AAPL   │ Apple Inc.   │  3.1T   │28.4 │147% │ ▁▂▄▆█    │
└────────────────────────────────────────────────────────┘
```

Details that matter for a data table:

- **Sticky header, frozen ticker column.** Non-negotiable at 15+ columns.
- **Column presets** — Valuation / Quality / Growth / Financials — plus a
  custom picker. Do not show 30 columns by default.
- **Virtualize past ~200 rows.** Server-side paginate past ~1,000.
- **Inline sparklines** for revenue or price. Cheap, and they make a wall of
  numbers scannable.
- **Density toggle.** Finance users want compact; new users want comfortable.
- **Null renders as `—`, never 0 or blank.** You already do this in the data
  layer; make the UI honor it and explain it on hover.
- **URL is the state.** Query, vintage, sort, columns, page.

**Gated actions appear but prompt to upgrade** — Export, Save, Alert, and the
vintage selector beyond one sample date. Showing a locked feature converts far
better than hiding it, as long as the lock is honest and one click from
resolution.

## 1.5 Company page

Sticky header: ticker, name, price, day change, and a persistent `As of [date]`
control. Tabs: **Overview · Financials · Valuation · Filings · Peers**.

The feature no competitor has: **provenance on hover.** Hovering any figure
shows the us-gaap tag it resolved from, the form it came from (10-K/10-Q), the
filing date, and a link to EDGAR. You already store all four fields.

The second feature no competitor has: a **restatement timeline** on the Filings
tab. "Revenue for Q1 2023 has been reported 3 times: $2.1B (May 2023), $2.4B
(Aug 2023), $2.3B (Feb 2024)." Your `facts` table already holds this and every
other screener throws it away.

## 1.6 Design system

Financial data UI has specific requirements that general design systems miss:

- **Tabular numerals everywhere.** `font-variant-numeric: tabular-nums`.
  Without it, columns of numbers visually jitter and look untrustworthy.
- **Two type families:** sans for chrome, mono for all numeric data.
- **Dark mode as default,** light available. Match the audience.
- **Color carries meaning, never decoration.** Green/red for direction only.
  Nulls in muted gray. No categorical rainbow.
- **8px spacing grid**, 4px inside dense table cells.
- **Density: compact = 32px rows, comfortable = 44px.**

You already have most of this in `base.html`. Extract it into a documented
token file before it drifts.

## 1.7 Mobile

Desktop-first is correct for the screener — nobody builds a 12-column screen on
a phone. But **company pages will get majority-mobile traffic** because that's
where search lands. So:

- Company pages: fully responsive, financial tables horizontally scrollable
  with a frozen label column.
- Screener on mobile: collapse to a card list showing ticker + three metrics,
  tap to expand. Do not try to render a 15-column grid.
- Landing page: fully responsive, hero query box works on mobile.

## 1.8 SEO — this is the growth plan, not an afterthought

Given no budget and no audience, programmatic SEO is the only realistic
acquisition channel. Design for it now, because it constrains routing and
rendering.

- **~5,000 ticker pages.** Unique title, description, and a generated summary
  paragraph from real data ("Apple's revenue grew 8% over 3 years while
  margins expanded..."). Thin duplicate pages get filtered — generate genuinely
  different text per company from the numbers you already have.
- **Programmatic list pages** from your own metrics: `highest-roe-stocks`,
  `lowest-pe-stocks`, `best-dividend-stocks`, `highest-fcf-yield`. Each is one
  query against `snapshot` and each targets a real search term.
- **Comparison pages**: `/compare/aapl-vs-msft`. High-intent, low-competition.
- **JSON-LD structured data** on company pages.
- **Sitemap generated from the database** on each nightly build.
- **TTFB under 200ms** — server-rendered and cached, which you get for free
  from the architecture above.

Realistic timeline: 3-6 months before meaningful organic traffic. Start
publishing pages early so indexing has time to work.

---

# 2. Backend

## 2.1 Feature tiers — what's free, what's paid, and why

| | Anonymous | Free account | Pro (~$12/mo) |
|---|---|---|---|
| Screener | 3 filters, 25 results | Unlimited filters, 100 results | Unlimited |
| Company pages | 5 years history | 10 years | Full history |
| Saved screens | — | 5 | Unlimited |
| Watchlists | — | 1 (20 stocks) | Unlimited |
| **Point-in-time screening** | 1 demo date | 1 demo date | **Full, any date** |
| **Screen backtesting** | — | — | **Yes** |
| Alerts (enter/exit a screen) | — | — | Yes |
| CSV export | — | 25 rows | Unlimited |
| Public API | — | — | 10k calls/mo |
| Restatement history | Preview | Preview | Full |

**The gating logic:** everything that makes the site useful *and indexable*
stays free, because free pages are the acquisition channel. What's paid is the
differentiator — point-in-time and backtesting — which is also genuinely the
most expensive thing to compute and store.

Price at $10-15/mo. Below Stockopedia (£30) and Koyfin ($39), above free.
Annual at ~2 months off.

## 2.2 Architecture changes required for production

The current stack is a correct MVP and wrong for deployment in five specific
ways:

**1. SQLite → Postgres.** SQLite is fine for reads, but nightly ingest writes
for minutes while users read, and you'll want connection pooling and a managed
backup. The schema was written portable for exactly this. Neon or Supabase free
tier is enough to start.

**2. Nothing is cached.** Screen results change once a day. Cache by
`hash(query, vintage, sort, page)` with a TTL that expires at the next nightly
build. Redis, or Postgres as a cache table if you want fewer moving parts.
This turns your most expensive endpoint into a key lookup.

**3. No background jobs.** The nightly chain — ingest → normalize → snapshot →
build vintage → invalidate cache → send alerts — needs a scheduler. Start with
APScheduler in-process; move to a real queue when alerts get slow.

**4. No auth.** Sessions + OAuth (Google, GitHub). Avoid rolling password auth
if you can — password reset flows, breach handling, and email deliverability
are unglamorous time sinks.

**5. No observability.** Sentry for errors, structured JSON logs, and one
health endpoint that checks data freshness. The failure you will actually hit
is a silent one: the nightly ingest breaks and the site serves stale numbers
for a week. Alert on `max(filed) older than N days`, not just on 500s.

Proposed production stack — all with usable free tiers:

| Concern | Choice |
|---|---|
| Host | Fly.io or Railway (~$5-15/mo) |
| Database | Neon or Supabase Postgres |
| Cache | Upstash Redis |
| Jobs | APScheduler → Celery/RQ if needed |
| Auth | Authlib + server sessions |
| Payments | Stripe, or LemonSqueezy (handles VAT/GST as merchant of record) |
| Email | Resend or Postmark |
| Errors | Sentry |
| Analytics | Plausible or Umami (privacy-friendly, no cookie banner) |

## 2.3 API design

Ship a public read API as a Pro feature. It's cheap — you already have every
endpoint — and it's a real differentiator for the technical segment of your
audience.

```
GET /v1/screen?q=roe>15&as_of=2019-03-31&limit=100
GET /v1/stocks/{ticker}
GET /v1/stocks/{ticker}/financials?period=annual
GET /v1/stocks/{ticker}/filings
GET /v1/backtest?q=...&from=2015-01-01&to=2024-01-01&rebalance=quarterly
```

Key-based auth via header, per-key rate limits, usage visible in the account
page. Version from day one (`/v1`) — cheap now, painful to retrofit.

## 2.4 The feature that justifies the price: backtesting

Point-in-time data is the input; **backtesting is the product**. Given a screen
and a rebalance cadence, replay it across every vintage and report what the
basket would have done.

```
POST /v1/backtest
  { q: "roe > 20 and pe < 15", from: "2015-01-01",
    rebalance: "quarterly", weighting: "equal" }
→ { cagr, max_drawdown, hit_rate, vs_spy, holdings_by_period[] }
```

This is a meaningful build — you need forward returns per holding period,
survivorship handling, and honest treatment of delisted names. But you already
have the hard part (the vintages), and it is the single clearest reason someone
pays you rather than using Finviz.

Be rigorous about caveats in the UI: no transaction costs, no slippage, no
taxes, and past performance is not predictive. Overstating backtest results is
both a credibility risk and a regulatory one.

## 2.5 Abuse, cost control, and rate limits

A screener is trivially scrapeable — your whole value is a queryable database.
Expect it.

- Rate limit anonymous users per IP, accounts per user, API per key.
- Cap result sets and require an account past a modest limit.
- Cache aggressively so scraping is cheap for you to serve.
- Accept that determined scraping will happen; the mitigation is that
  point-in-time data is expensive to reconstruct without your `filed` history.

## 2.6 Legal — read this before charging money

Not legal advice, and worth an hour with an actual lawyer before you take
payment. Three specific things:

**1. Investment adviser status.** In the US, publishing data and generic
screening tools generally falls under the publisher's exemption to the
Investment Advisers Act. Personalized recommendations do not. Keep the product
descriptive ("companies matching your filters") and never prescriptive ("buy
this"). Backtest results are a gray area — present them as historical
statistics, not as a strategy recommendation.

**2. Price data redistribution.** SEC fundamentals are public domain and yours
to redistribute freely. **Prices are not.** Most vendors' terms forbid bulk
redistribution, which directly affects your CSV export and public API. Read
EODHD's terms before shipping either. You may need to export fundamentals
freely but restrict price history.

**3. Disclaimers.** Clear, on every page, not buried. "Not investment advice.
Data may contain errors. Verify against primary filings." You already link to
EDGAR — that's a genuine mitigation, lean on it.

---

# 3. Build order

Each phase should end deployable.

**Phase A — make it real (2-3 weeks)**
Full-market ingest. Fix coverage gaps found on `/coverage`. Migrate to
Postgres. Deploy to Fly. Nightly job. Sentry + freshness alert.
*Done when:* the live site has 5,000 real companies and updates itself.

**Phase B — the acquisition surface (2-3 weeks)**
Rebuild the landing page. `/stocks/{ticker}` with generated summaries. Screen
and list pages. Sitemap, JSON-LD, meta tags. Mobile responsive.
*Done when:* Google has indexed a few thousand pages.

**Phase C — accounts (1-2 weeks)**
OAuth, sessions, saved screens, watchlists, tier enforcement.
*Done when:* someone can sign up and their screens persist.

**Phase D — monetization (2-3 weeks)**
Stripe, gate point-in-time and export, pricing page, account management.
*Done when:* you can take money.

**Phase E — the moat (3-4 weeks)**
Backtesting engine, alerts, restatement timeline, public API.
*Done when:* there's a reason to pay beyond convenience.

Rough total: 3-4 months of steady evenings and weekends. Phases A and B are
non-optional; C through E only make sense if A and B produce traffic.

---

# 4. Open decisions

These need your call and each one changes the build:

1. **Free tier generosity.** More free = more SEO surface and slower revenue.
   My lean: generous, because traffic is the bottleneck, not conversion.
2. **Backtesting vs. alerts first.** Backtesting is the stronger differentiator
   but is roughly 3x the work. Alerts are easier and drive retention.
3. **Do you want the API at all?** It's a small segment and it makes scraping
   trivially legitimate. Defensible either way.
4. **Brand and domain.** "us-screener" is a working title. Pick something you'd
   be happy putting on a landing page before Phase B.
5. **Is this still a portfolio project or a business?** They diverge at Phase
   C. A portfolio project should skip auth and payments entirely and spend that
   time on the backtesting engine, which is far more interesting to show.
