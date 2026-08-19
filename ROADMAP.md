# Building a US screener — plan

## Stack (you asked me to pick)

**Python + FastAPI backend, Postgres, Next.js frontend.**

Not because it's trendy — because of a specific asymmetry in this project. It's
~70% data engineering and ~30% web app. The hard, interesting, portfolio-worthy
part is the pipeline, and Python's ecosystem there (pandas, the mature XBRL
libraries, Airflow/Prefect when you need scheduling) has no real TypeScript
equivalent. Meanwhile the frontend is a table with filters — genuinely easy in
any framework.

The common alternative, all-TypeScript Next.js, saves you one language but makes
the hard part harder. Take the split.

Concretely:

| Layer | Choice | Why |
|---|---|---|
| Ingest/ETL | Python, stdlib + `httpx` | What's in this repo already |
| DB | SQLite → Postgres | SQLite for the first month; migrating is a day's work and the schema is already portable |
| API | FastAPI | Auto-generated OpenAPI docs, async, and it's Python so it shares code with the pipeline |
| Frontend | Next.js + TypeScript + TanStack Table | Server components render the screener results fast; TanStack handles sorting/virtualization for 5,000 rows |
| Charts | Recharts or visx | Recharts is faster to learn |
| Hosting | Fly.io or Railway | Both give you a Postgres and a cron in one place, ~$5–15/mo |

**Do not start with Postgres.** SQLite in WAL mode handles a screener's read
load fine into the thousands of users, needs zero ops, and the file is easy to
back up. Move when you have a reason.

---

## Phase 0 — the pipeline ✅

This repo. Ingests SEC XBRL, normalizes it, computes ratios, screens. Run the
tests to see the three data problems it solves.

## Phase 1 — make it a website (weekend 1–2)

1. `pip install fastapi uvicorn`, wrap `screener.screen.run()` in a
   `GET /api/screen?q=...` endpoint. The query parser is already
   injection-safe, so this is mostly plumbing.
2. `GET /api/company/{ticker}` returning the `fundamentals` history.
3. Next.js page: a text box, a table, a loading state. Ship it ugly.

**Milestone:** you can type `roe > 20 and pe < 15` in a browser and get rows.

## Phase 2 — make it good (weeks 3–6)

- **Full-market load.** Pull `companyfacts.zip` and ingest all ~10k filers.
  Expect surprises: REITs, banks, and insurers use tags your map doesn't have.
- **Coverage dashboard.** For each metric, what % of companies resolved? This
  is the single most useful internal tool you'll build. Every gap is a missing
  entry in `concepts.py`, and you cannot find them by reading filings manually.
- **Company page:** 10-year P&L / balance sheet / cash flow tables, charts,
  links back to the source filing on EDGAR.
- **Saved screens + shareable URLs.** Encode the query in the URL; costs you
  nothing and it's how people share screeners.
- **Nightly cron:** ingest → normalize → snapshot.

## Phase 3 — earn the right to exist (months 2–4)

Cloning screener.in for the US means competing with Finviz, Stockopedia,
Koyfin, Simply Wall St, and stockanalysis.com — several of which are free and
well-funded. You need one thing they do badly. Candidates, roughly in order of
how much I'd bet on them:

- **Filing-diff alerts.** "Show me what changed in the risk factors between
  this 10-K and last year's." The data is right there in EDGAR, nobody
  presents it well, and it's the kind of thing that gets shared.
- **Restatement history.** You're already storing every version of every fact.
  "This company has restated revenue 4 times in 3 years" is a real signal that
  no consumer screener surfaces, because they all overwrite.
- **Screen the footnotes, not just the numbers.** Segment data, customer
  concentration, lease obligations — all XBRL-tagged, all ignored by
  ratio-only screeners.
- **Point-in-time screening.** "What would this screen have returned in
  March 2019, using only data filed by then?" Your `filed` column makes this
  possible and it's the #1 complaint about every free screener (they're all
  contaminated by lookahead bias).

Weakest option: another ratio screener with a nicer UI. The UI advantage
evaporates in a month.

## Phase 4 — scale

- Materialize `snapshot` per day rather than overwriting → free history.
- Postgres + a read replica when the nightly job starts blocking reads.
- Redis/HTTP caching in front of the screen endpoint; screening results change
  once a day, so cache them for a day.
- Move ingest to a queue (Celery/RQ) so one hung request can't stall the run.

---

## Data sourcing, concretely

**Fundamentals — free, forever, no catch.**
SEC EDGAR XBRL APIs. No key, no registration, no rate tier. Requirements: a
`User-Agent` header with a real contact email, and ≤10 requests/second. For
full-market loads use the nightly bulk zip instead of 10,000 API calls.

The data is a US-government work and therefore **public domain — you can
redistribute it**. This is a much better position than Indian screeners are in,
and it's the main reason this project is viable solo.

Worth being concrete about how much better. screener.in does not parse filings
itself — every company page carries the line "Data provided by C-MOTS Internet
Technologies Pvt Ltd" in the footer. C-MOTS is a Mumbai data vendor (founded
1997, ~260 staff, empanelled with BSE/NSE/MCX/NCDEX) whose product is exactly
the normalization layer this repo builds in `concepts.py`: taking inconsistent
filings and emitting one comparable schema. India has no free machine-readable
equivalent of the SEC's XBRL APIs, so buying that feed is the rational move
there, and it means screener.in carries a recurring per-company data cost.

Two consequences for this project:

1. **The moat argument is confirmed by the market.** Normalization is hard
   enough that the best free screener in India outsources it. Building it
   in-house is the differentiating asset, not incidental plumbing.
2. **You have a structural cost advantage screener.in does not.** SEC XBRL
   hands you for free the thing they pay a vendor for. Your only recurring
   data cost is prices (~$20/mo). Don't squander that by reaching for a paid
   fundamentals vendor out of habit — the free source here is the better one.

Note that screener.in still goes direct to BSE/NSE for documents — annual
reports, announcements, credit ratings, concall PDFs are all linked straight
from the exchanges. So the real architecture is hybrid: licensed vendor feed
for the standardized financial tables, direct exchange scraping for everything
document-shaped. Your equivalent split is EDGAR XBRL for the numbers and EDGAR
filing archives for the documents — both free.

**Prices — this is what you'll pay for.**

| Source | Cost | Trade-off |
|---|---|---|
| Stooq | free | Undocumented, unsupported, no ToS you'd want to rely on. Fine for dev. |
| EODHD | ~$20/mo | Documented, splits/dividends, delisted tickers. **Best fit for your budget.** |
| Tiingo | ~$10–50/mo | Great for research; academic pricing if you qualify |
| Polygon | ~$99/mo | Real-time, tick data. Overkill until you have users |

Start on Stooq, switch to EODHD before you show anyone. The provider interface
in `prices.py` is already pluggable.

**Two licensing traps worth knowing now:**

1. **Real-time quotes are not like fundamentals.** Displaying live NYSE/Nasdaq
   prices publicly requires an agreement with the exchange and per-user
   reporting fees. 15-minute-delayed or end-of-day data avoids this entirely.
   Build on EOD; you don't need live prices for a fundamentals screener anyway.
2. **Most price vendors forbid redistribution.** You can show a chart; you
   generally cannot offer a "download all prices" button. Read the ToS before
   you build an export feature.

**Not legal advice** — but worth flagging: in the US, publishing data and
generic screening tools generally falls under the publisher's exemption to the
Investment Advisers Act. Making personalized recommendations does not. Keep the
product descriptive ("here are companies matching your filters"), not
prescriptive ("buy this"), and add a disclaimer.

---

## Things that will surprise you

- **Financials don't fit.** Banks have no revenue or gross margin in the normal
  sense; insurers report premiums. Either special-case SIC codes 6000–6799 or
  exclude them and say so.
- **Fiscal years aren't calendar years.** Apple's FY2024 ends in September.
  Comparing "FY2024" across companies is comparing different time periods. The
  `frame` field in EDGAR gives you SEC's calendar-aligned mapping.
- **52/53-week fiscal calendars** mean a "quarter" is 84 or 91 or 98 days.
  Hence the ranges in `PERIOD_BANDS` rather than exact matches.
- **Negative equity breaks ROE.** So does negative EPS for P/E. Return null,
  don't return a nonsense number — the code already does this and it's the
  reason `_div` exists.
- **Coverage decays going back in time.** XBRL was phased in 2009–2011. Before
  that you're parsing HTML, which is a different and much worse project.
- **~40% of tickers in `company_tickers.json` are ETFs, trusts, and shells.**
  Filter on SIC code and the presence of actual financials.

---

## First three things to do

1. `export SEC_USER_AGENT="Rudra Arora rudraarora120005@gmail.com"` then run the
   README's quickstart against 50 real companies. Look at what breaks.
2. Write the coverage dashboard (Phase 2). It will tell you what to fix next
   better than any plan I can write.
3. Pick your Phase 3 differentiator *before* you build the UI. It changes what
   you store.
