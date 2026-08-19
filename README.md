# us-screener

> **Picking this up fresh, or handing it to another model? Read
> [HANDOFF.md](HANDOFF.md) first.** It has the full context: architecture,
> the SEC data quirks that will otherwise cost you days, every design decision
> and its reasoning, the backlog, and what's blocked on what.


A screener.in-style fundamentals screener for US equities, built on free SEC
EDGAR XBRL data — with **point-in-time screening**, which no free screener
offers.

Pipeline (stdlib only) + FastAPI/HTMX web app. See [ROADMAP.md](ROADMAP.md).

## See it running in 30 seconds

No SEC account, no API key, no network:

```bash
pip install fastapi uvicorn jinja2 httpx
python -m screener.cli --db demo.db demo      # seed from fixtures
python -m screener.cli --db demo.db serve     # http://127.0.0.1:8000
```

## Real data

```bash
export SEC_USER_AGENT="Your Name you@example.com"   # SEC 403s you without this

python -m screener.cli init
python -m screener.cli tickers                       # ~10k companies
python -m screener.cli ingest --limit 50             # XBRL facts
python -m screener.cli prices --limit 50             # EOD prices (stooq, free)
python -m screener.cli normalize                     # -> canonical metrics
python -m screener.cli snapshot                      # -> ratios
python -m screener.cli history --since 2018-01-01    # -> point-in-time vintages

python -m screener.cli screen "roe > 15 and pe < 25 and market_cap > 1b"
python -m screener.cli screen "roe > 15" --as-of 2019-03-31
python -m screener.cli company AAPL
python -m screener.cli serve
```

Full market load (recommended over 10,000 API calls):

```bash
curl -A "$SEC_USER_AGENT" -O \
  https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
python -m screener.cli ingest --zip companyfacts.zip
```

## Point-in-time screening

Run any screen against a past date and get what it *would* have returned then,
using only filings that existed on that date.

```bash
python -m screener.cli screen "roe > 20 and pe < 15" --as-of 2019-03-31
```

Three cutoffs have to agree, and getting any one wrong reintroduces the
lookahead bias this exists to remove:

1. **Filings** — `filed <= as_of`, not `period_end <= as_of`. A FY2023 10-K is
   filed in late Feb 2024; nobody screening in January could see it.
2. **Prices** — last close on or before the date, not today's price.
3. **Universe** — only companies that had filed by then, so companies that
   IPO'd later don't appear.

The payoff is that restatements stay where they belong. In the test fixtures a
quarter is restated 50% higher a year after the fact; today's view shows the
restated figure, a vintage dated before the amendment shows the original. Every
free screener shows you today's restated number in both places, which is why
their historical screens backtest better than reality.

This is only possible because `facts` keeps every version of every datapoint
with its filing date. It cannot be retrofitted onto a pipeline that overwrites.

## Web app

| Route | What |
|---|---|
| `/` | Screener: query box, example chips, sortable results, vintage selector |
| `/company/{ticker}` | Ratios, 10-year history, revenue chart, tag provenance |
| `/coverage` | Per-metric coverage dashboard — your work queue for `concepts.py` |
| `/api/docs` | OpenAPI docs for the JSON API |

Server-rendered HTML with HTMX for interactivity. One process, no build step,
no CORS. The JSON API under `/api` returns the same data through the same code
paths, so a React frontend later is additive rather than a rewrite.

## Query syntax

```
roe > 20 and pe < 15
market_cap > 10b and revenue_growth > 12 and debt_to_equity < 0.5
net_margin > 25 or gross_margin > 60
```

Numbers accept `k`/`m`/`b`/`t` suffixes. Fields are allowlisted in
`screener/screen.py` — run `screen "help > 0"` to get the list in the error.

## Tests

```bash
python tests/test_pipeline.py     # 18 tests: data correctness
python tests/test_web.py          # 11 tests: routes, rendering, API
```

29 tests against synthetic fixtures that reproduce real EDGAR behaviour —
different revenue tags per company, a restatement filed a year late, no Q4
filings, YTD-only cash flow, a bank with no cost of revenue, negative equity,
and a September fiscal year end. No network needed.

## The three problems this actually solves

A screener is easy to build badly. These are the failure modes that don't
announce themselves — your site returns plausible-looking numbers that are
wrong, and you find out when a user emails you.

**1. Every company tags revenue differently.** XBRL standardizes the *format*,
not the *vocabulary*. Apple uses
`RevenueFromContractWithCustomerExcludingAssessedTax`, Ford uses `Revenues`, a
2013 10-K uses the now-deprecated `SalesRevenueNet`. Screen on one tag and half
the market silently returns NULL. `screener/concepts.py` is the mapping table —
this is the part commercial vendors actually sell.

**2. Nobody files a Q4.** US companies file three 10-Qs and one 10-K. There is
no Q4 income statement anywhere in EDGAR. "Sum the last four quarters" gives
you three quarters of this year plus one from last year — about 7% off in
testing, which is small enough that nobody notices and large enough to make
every margin and P/E on your site wrong. Q4 has to be derived.

**3. Cash flow is filed year-to-date.** A 10-Q's cash flow statement covers
6 or 9 months, not the quarter. Discrete quarters come from differencing
consecutive YTD periods that share a fiscal-year start.

Plus restatements: the same quarter is reported many times (original 10-Q,
restated in the 10-K, again as a comparative). Every version is kept in `facts`
and resolved by filing date — which also means you can screen on restatement
history, something no consumer screener offers.

## Layout

```
schema.sql              facts -> fundamentals -> snapshot + snapshot_history
screener/
  concepts.py           us-gaap tag -> canonical metric mapping   ← the moat
  edgar.py              SEC client (rate limits, User-Agent) + parser
  ingest.py             live API, bulk zip, and local-directory loaders
  transform.py          restatements, Q4 derivation, YTD differencing,
                        ratios, point-in-time vintages
  prices.py             EOD prices — stooq (free) / EODHD (~$20/mo)
  screen.py             query parser -> parameterized SQL (allowlisted)
  cli.py                init/tickers/ingest/prices/normalize/snapshot/
                        history/screen/company/serve/demo
web/
  app.py                FastAPI routes: pages, HTMX partials, JSON API
  templates/            base, index, _results, company, coverage
tests/
  make_fixtures.py      synthetic filings reproducing the quirks above
  test_pipeline.py      data correctness
  test_web.py           routes and rendering
```

## SEC etiquette

Non-negotiable, and they enforce both: a `User-Agent` header with a real
contact email, and ≤10 requests/second. `edgar.py` handles the throttle. The
data itself is a US-government work — public domain, redistributable.

## License

MIT for this code. SEC data is public domain. Price data is subject to your
provider's terms — see ROADMAP.md before building an export feature.
