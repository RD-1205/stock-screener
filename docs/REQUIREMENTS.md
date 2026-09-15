# Requirements backlog

Running record of agreed product requirements. Nothing here is built yet —
this exists so decisions don't get lost between sessions.

Status: `[ ]` not started · `[~]` in progress · `[x]` done

---

## R1 — Landing page is a market news page, not a marketing page

> "the landing page should be a news page affecting various listed market
> companies with basic options"

- [x] **R1.1** Market news feed as the primary landing content — done
      2026-09, `screener/news.py` + homepage "Market news" section
- [ ] **R1.2** News items tagged to the companies they affect, linking to
      `/stocks/{ticker}`
- [ ] **R1.3** Basic filter controls on the feed — sector, market cap band,
      event type, "my watchlist"
- [ ] **R1.4** Market overview strip: index levels, breadth, biggest movers
- [ ] **R1.5** Feed paginates / infinite-scrolls without a full page reload

**Engineering note (updated 2026-09):** the real ask is general market-moving
news first (macro, geopolitical, rates — "US 20yr hit 5%", not just company
press releases), with SEC filing events (8-K decoded, Form 4, 13D/G) as a
tagged category layered on top, not the primary source. See `DESIGN-SPEC.md`
§1.1 for the full writeup and the reasoning that got flipped.

## R2 — Big, beautiful, interactive search on the home screen

> "a search option on the home screen making it look like beautiful and big and
> interactive, where they can search for any company/ticker"

- [ ] **R2.1** Large hero search, visually dominant
- [ ] **R2.2** Instant results as you type (target < 100ms perceived)
- [ ] **R2.3** Match on ticker *and* company name, fuzzy and typo-tolerant
- [ ] **R2.4** Rich result rows — ticker, name, sector, price, day change, mini
      sparkline
- [ ] **R2.5** Full keyboard control: arrows, enter, escape; `⌘K` / `Ctrl+K`
      opens it from anywhere on the site
- [ ] **R2.6** Empty state shows recent searches, then trending / most-viewed
- [ ] **R2.7** Persists as a compact search in the header on every other page

## R3 — Company page, chart first

> "company page should be share price graph of course the first thing and then
> sections like news, shareholding, financials, all of them interactive where
> applicable"

- [ ] **R3.11** **Business / revenue segments** — how many businesses, what %
      of revenue from each, historical trend chart (Rudra, this session).
      **Blocked on a new ingestion path**, see note below.
- [x] **R3.1** Share price chart is the first thing below the header
- [x] **R3.2** Range selector — 1M / 6M / 1Y / 5Y / 10Y / MAX
- [x] **R3.3** Crosshair with a synced legend showing value at cursor
- [ ] **R3.4** Comparison overlay — add a peer or an index to the same chart
- [ ] **R3.5** Event markers on the chart — earnings dates, 8-K filings,
      insider transactions
- [ ] **R3.6** **News section** — company-specific, newest first
- [ ] **R3.7** **Shareholding section** — institutional (13F), >5% holders
      (13D/G), insider activity (Forms 3/4/5), with change-over-time chart.
      **Blocked on a new pipeline** — none of this is in `companyfacts`. It
      needs the 13F structured datasets plus Forms 3/4/5 XML. ~2 weeks.
- [~] **R3.8** **Financials section** — annual/quarterly toggle ✅, YoY + QoQ
      growth ✅, ratio history by year (ROE/ROCE/D/E…) ✅, peers ✅.
      Still to do: expandable line items, click-a-row-to-chart, hover
      provenance popovers
- [ ] **R3.9** Sticky sub-navigation between sections
- [ ] **R3.10** Everything interactive where interaction adds meaning — no
      motion for its own sake

**Why segments (R3.11) can't be built from what we ingest today.** Segment
revenue *is* in XBRL, but it lives in the *dimensional* facts — a revenue tag
qualified by `us-gaap:StatementBusinessSegmentsAxis` or
`srt:ProductOrServiceAxis` members. The `companyfacts` API, which is the whole
basis of our current pipeline, returns only **consolidated, undimensioned**
facts. Segment breakdowns are simply not in the response, at any depth.

Getting them means a second ingestion path — either the SEC's *Financial
Statement and Notes Data Sets* (which include a `num.txt` carrying the
dimensional qualifiers) or parsing raw XBRL instance documents per filing.
Both are free; both are real work, and the segment member names are
company-defined strings that need their own normalization layer, exactly like
`concepts.py` does for line items. Estimate: ~1.5–2 weeks, and it should be
scheduled alongside R3.7 since both need the same new plumbing.

## R4 — Carried forward from earlier sessions

- [ ] **R4.1** Point-in-time screening remains the paid differentiator
- [ ] **R4.2** Provenance on hover — every figure traceable to its filing
- [ ] **R4.3** Restatement timeline per company
- [ ] **R4.4** Screener with dual-mode query (text DSL + chip builder)
- [ ] **R4.5** Programmatic SEO surface — `/stocks`, `/screens`, `/lists`,
      `/compare`
- [ ] **R4.6** Backtesting engine
- [ ] **R4.7** Accounts, saved screens, watchlists, alerts
- [ ] **R4.8** Postgres, caching, background jobs, observability

## R5 — Market strip: keep the indices, add a rotating ticker tape

> "I really like the S&P and Nasdaq ticker you have, those should stay as is,
> but next to it in the same line a rotating live ticker stock action — if
> people save stocks in the watchlist it should have an option to have those
> over there"

- [ ] **R5.1** Index summary (S&P / Nasdaq) — still to build, now lives in
      the landing page's market pulse band rather than beside the belt
- [x] **R5.2** ~~Same line as the indices~~ — **changed.** Now a full-width
      belt hanging directly under the header, site-wide (Rudra, this session)
- [x] **R5.3** Each tape item: ticker, price, change, direction colour; clicks
      through to `/stocks/{ticker}`
- [x] **R5.4** ~~Explicit pause button~~ — **dropped.** Pause on hover and on
      keyboard focus instead, plus a "reduce motion" setting (see note)
- [ ] **R5.5** Source toggle on the tape — Watchlist / Most active / Biggest
      movers / Megacaps
- [ ] **R5.6** Defaults to the user's watchlist when they have one, falls back
      to Most active when they don't
- [~] **R5.7** Watchlist works for anonymous users via localStorage (belt
      reads it); migration on signup waits for accounts
- [x] **R5.8** Prices refresh in place without reloading or restarting the
      scroll animation
- [x] **R5.9** Mobile: belt is already its own row; label collapses

**Decision — no visible pause button (Rudra, this session).** Agreed, it's
visual clutter on a decorative strip. The accessibility obligation still has to
be met some way, so it's met three quieter ways instead:

1. `prefers-reduced-motion: reduce` renders the tape **static** — no animation
   at all for users whose OS says they need that. This covers the people the
   rule exists for.
2. Scrolling pauses on hover **and on keyboard focus** (`:focus-within`), so
   anyone trying to read or click an item can stop it without a button.
3. A "reduce motion" toggle lives in account settings and persists.

WCAG 2.2.2 asks for *a mechanism*, not *a button*. Hover/focus pause plus an
OS-level and in-app preference is a defensible reading, and it's what most
well-built sites ship. Keep the tape supplementary — never put information
there that appears nowhere else.

**Decision — price source: Finnhub 15-minute delayed (Rudra, this session).**
Real-time US quotes carry exchange licensing fees; 15-minute delayed data does
not. Finnhub's free tier (60 req/min) is ample for a tape.

Implemented in `screener/quotes.py` as a swappable adapter:

- `FINNHUB_API_KEY` set → delayed quotes, labelled **"Delayed 15 min"**
- no key → last close from our own `prices` table, labelled **"At close"**

Both paths always label their source, so the UI can never present delayed data
as live. **Action needed:** grab a free key at finnhub.io/register and set
`FINNHUB_API_KEY` — until then the tape and company header run on last close.

## R6 — Market sentiment gauge

> "I also really like the market sentiment meter, that is greed and so on —
> maybe have that somewhere on the landing as well"

- [ ] **R6.1** A 0–100 sentiment gauge on the landing page, in the market
      pulse band, divided into **5 zones of 20 points** (see decision below)
- [ ] **R6.2** Computed in-house from published inputs — **not** scraped from
      CNN, and **not** named "Fear & Greed" (that's CNN's brand)
- [ ] **R6.3** Component breakdown visible on click — every sub-indicator, its
      value, and its contribution
- [ ] **R6.4** Historical series with a chart — "where we were a month ago"
- [ ] **R6.5** Methodology page documenting every input and the maths
- [ ] **R6.6** **Ties into point-in-time:** "screen the market as it was the
      last time sentiment was this low"

R6.6 is the reason to build this rather than embed someone else's widget. It's
the only sentiment gauge that can be wired to a screener with real filing-date
history, and it makes the differentiator visible on the landing page — which
resolves the "differentiator is never visible" risk flagged below.

**Decision — zone count.** You asked 4 or 6; I'd argue for **5**, and the
reason is about noise rather than aesthetics.

| Zones | Points each | Verdict |
|---|---|---|
| 4 | 25 | No neutral. Every reading is forced to be directional, which over-claims when the index is genuinely mid-range. |
| **5** | **20** | **Recommended.** True neutral centre; 20 points is wider than typical daily movement, so the label stays stable. |
| 6 | ~17 | Too fine. The index would flip zones on noise, and the label stops meaning anything. |

The gauge's job is to give a five-second read, and its credibility depends on
the label not flickering. A sentiment index legitimately spends much of its
time near the middle — an even zone count has to call that "slightly fearful"
or "slightly greedy", which reads as false precision.

Five also matches the mental model people already have from CNN. Familiarity
is a genuine feature for a gauge: it's understood at a glance with no legend.

```
0 ─────── 20 ─────── 40 ─────── 60 ─────── 80 ─────── 100
  Extreme     Fear      Neutral     Greed      Extreme
   fear                                         greed
```

Use your own wording if you'd rather not echo CNN's labels — "very cautious /
cautious / balanced / confident / very confident" carries the same structure
without borrowing their phrasing. If you still want an even count, take 4 over
6, and label the centre-adjacent zones conservatively.
---

## Positioning tension — flagged, needs a decision

R1–R3 describe a **stock research portal** (the screener.in / stockanalysis.com
shape). R4.1 describes a **point-in-time screening tool**. These are different
products with different competitors.

They are not in conflict if layered deliberately:

- **News + search + company pages = the acquisition layer.** High traffic,
  high SEO value, commodity features, table stakes.
- **Point-in-time + backtesting = the retention and revenue layer.** Low
  traffic, hard to build, the reason anyone pays.

That's a coherent strategy — get found on commodity content, keep and monetize
on the differentiator. The risk is spending all available time on the
acquisition layer and never shipping the differentiator, at which point you are
a worse stockanalysis.com.

**Mitigation:** keep at least one point-in-time surface visible on every
company page from day one, so the differentiator is never "coming soon."
