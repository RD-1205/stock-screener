# Pending changes

Queued edits to apply in one batch. Nothing here is implemented yet.

Add new items to the bottom. Each entry records what Rudra asked for, what I'd
actually do, and anything that would break — so the batch can be applied
without re-litigating the reasoning.

---

## P1 — Remove alphabetical *sorting* (keep the letter *filter*) ✅ Done 2026-09-08

**Asked:** "what could someone potentially have sorting companies
alphabetically, i don't think we need that"

**Agreed — with one distinction.** These were conflated in the current build
and they do different jobs:

| | What it does | Verdict |
|---|---|---|
| `sort=ticker` / `sort=name` | Reorders the visible list A–Z | **Drop.** Nobody arrives wanting this. If you know the name, search is faster; if you don't, alphabet tells you nothing. |
| `?letter=M` filter | Partitions 5,000 companies into 26 bounded pages | **Keep, demote.** This is a crawl-discovery mechanism, not a user feature — the value is a search engine reading it, not a human clicking it. |

**Third thing to fix while in here.** Paginating thousands of rows by volume is
unstable: volume changes between requests, rows shift across page boundaries,
and companies get silently skipped or duplicated. Ticker is the right
tiebreaker — but as a hidden secondary `ORDER BY`, never a user-facing option.

### Edits

1. `screener/browse.py` → `SORTS`: delete the `ticker` and `name` entries.
2. `screener/browse.py` → `browse()`: append `, s.ticker ASC` to every
   `ORDER BY` so pagination is deterministic.
3. `web/templates/pages/stocks_index.html`: move the **Alphabetical** group to
   the bottom of the browse menu, below Size.
4. `docs/SITEMAP.md`: note that `/stocks?letter=X` stays indexable and belongs
   in the sitemap — it's a hub, not a duplicate of `/stocks`.

### Risk

`tests/test_browse.py::test_menu_links_are_real_hrefs` asserts the presence of
the `Alphabetical` heading — it will still pass after a reorder, but check it.
No test currently depends on `sort=ticker`, so removing it is clean.

**Shipped:** `screener/browse.py` — dropped `ticker`/`name` from `SORTS`,
appended `, s.ticker ASC` as a hidden secondary `ORDER BY` on every query.
The Alphabetical group was already the last item in `stocks_index.html`
(nothing to move). `docs/SITEMAP.md` now documents `/stocks?letter=X` as
indexable/in-sitemap, distinct from the `noindex` sort/sector/band/page
combinations. 13/13 `test_browse.py` still passing.

---

## P2 — Collapse the browse filters behind a Filters button ✅ Done 2026-09-08

**Asked:** "don't have the filters outright visible, hide them under a filter
button which can be accessed"

**Agreed, with one constraint that changes the implementation.** The browse
menu is currently the main way search engines discover deep pages (sector,
size and index hubs). If the button *builds* the panel with JavaScript on
click, those links don't exist in the HTML and the crawl surface disappears.

So: **always render the links in the DOM, toggle visibility with CSS.** The
panel is `hidden` by default and a button flips it. Crawlers read the markup;
users see a clean page.

### Edits

1. `web/templates/pages/stocks_index.html`: wrap `.browse-menu` in a
   `<div id="filters" hidden>`; add a `Filters` button plus a live count of
   how many are applied (`Filters · 2`).
2. Keep the active-filter chips **outside** the panel and always visible —
   otherwise a user can't see what's filtering their results.
3. `web/static/css/base.css`: `.browse` becomes single-column when the panel
   is closed; the panel opens as a bar above the table on desktop, a sheet on
   mobile.
4. `web/static/js/app.js`: ~10 lines to toggle `hidden` and `aria-expanded`.
   Panel stays open if any filter is active on page load.

### Risk

`test_menu_links_are_real_hrefs` must keep passing — the assertion that
menu entries are plain `<a href>` is exactly what protects the crawl surface
here, so do **not** relax it.

**Shipped, with one deliberate simplification.** Filters live behind a
`Filters · N` toggle (`data-filters-toggle` / `~10 lines in app.js`); all
links stay real `<a href>`s in the DOM at all times (`hidden` only toggles
CSS visibility), so `test_menu_links_are_real_hrefs` still passes unchanged.
Active-filter chips render outside the panel, always visible. Simplified
from the original two-layout spec ("bar on desktop, sheet on mobile") to one
layout at every width — a full-width horizontal bar above the results,
reusing the auto-fit grid that used to be mobile-only — since that already
satisfies "opens as a bar above the table" without a second, modal-style
implementation for mobile. **Found and fixed a real bug while building
this:** `[hidden]` was being silently overridden by `.browse-menu { display:
grid }` — same CSS specificity, later in the cascade wins, so the browser's
default `[hidden]{display:none}` never applied. Added an explicit
`.browse-menu[hidden] { display: none }` (and the same fix for the P4
dropdown menu below, which had the identical bug).

---

## P3 — Market cap tiers: nothing is actually broken

**Asked:** "size only has large and midcap, why not small… how about a live
market cap calculator and our own tier deciders which shift tiers"

**No change needed — this already works the way you're describing.** Two
things were being conflated:

**Why you only see two bands.** All five tiers exist in `MCAP_BANDS`. The menu
hides bands with zero companies, and all six *fixture* companies happen to
land between $7B and $81B:

```
MODT  81.4B → Large     DEPR  12.4B → Large
BANQ  46.3B → Large     NEGE  10.4B → Large
SEPT  36.2B → Large     LEGC   7.4B → Mid
```

Ingest 5,000 real companies and all five populate — the median US listed
company is well under $2B, so Small and Micro will be the *biggest* buckets.
This is a fixture artifact, not a data limitation.

**Tiers already shift automatically.** Market cap is computed, not stored as a
label: `price × shares_outstanding`, recalculated every time the snapshot is
rebuilt. Bands are evaluated live from that number at query time. A company
crossing $10B moves from Mid to Large on the next build with no manual
reclassification. There is no static tier column to go stale.

### One genuine improvement available

Market cap currently uses the **last close** from our `prices` table. Once
`FINNHUB_API_KEY` is set we could compute it from the delayed quote instead,
making tiers current to ~15 minutes rather than to last night.

Worth doing, but note the trade-off: market cap would then change during the
session, so band membership could flip mid-day and a cached page would
disagree with a fresh one. Recommend recomputing on the snapshot build only,
and showing the live-quote market cap on the **company page** where it's a
single number rather than a filter boundary.

---

## P4 — Screener: range filters instead of typed comparisons ✅ Done 2026-09-08

**Asked:** "people can sort and look for companies with specific metric
values… let's make it more user friendly where we give them lower and upper
bounds for metrics. Only lower or only upper gives all companies above or
below."

**Agreed — this is the right primary interface.** The text DSL
(`roe > 15 and pe < 25`) is powerful but it's a blank box: a new user has no
idea what to type, what's available, or what a reasonable value is. A row of
min/max inputs answers all three by existing.

### Design

```
┌────────────────────────────────────────────────────────┐
│ Metric        Min        Max                           │
│ P/E         [     ]    [ 25  ]   ← blank min = no floor│
│ ROE %       [ 15  ]    [     ]   ← blank max = no cap  │
│ Market cap  [ 1b  ]    [     ]                         │
│ + Add metric ▾                          [ Run screen ] │
└────────────────────────────────────────────────────────┘
```

- Both blank → the metric isn't filtered at all (not "0 to ∞").
- Min only → `metric >= min`. Max only → `metric <= max`.
- Suffixes work in the boxes exactly as in the DSL: `1b`, `500m`.
- Each row shows the metric's actual **range across the universe** as
  placeholder text (`P/E · typically 5–45`), computed from the snapshot. This
  is the single biggest usability win — it stops people typing values that
  match nothing.
- `+ Add metric` offers the allowlist grouped as Valuation / Quality / Growth
  / Size.

### The text DSL stays, as a secondary view

One representation, two editors — same rule we agreed for the chip builder.
The ranges compile **to** the DSL string, which stays the source of truth, so
the URL remains shareable and the existing parser and its injection tests keep
working unchanged.

### Edits

1. `screener/screen.py`: add `compile_ranges(dict) -> str` producing DSL text.
   No change to `compile_query` — ranges become `and`-joined comparisons.
2. `screener/screen.py`: add `metric_ranges(conn)` returning p5/p95 per
   allowlisted column, for placeholders. Cache it.
3. `web/templates/index.html`: replace the single text input with the range
   grid; keep the DSL box under an "Advanced" toggle showing the compiled
   query live.
4. Handle `NULL` explicitly: a company with no P/E must be **excluded** from a
   P/E filter, not treated as zero. The current SQL already does this via
   `IS NOT NULL`; add a test so it can't regress.

### Risk

The DSL has no `>=` / `<=` distinction problem — both already parse. But
`compile_ranges` must emit `>=`/`<=` (inclusive), not `>`/`<`, or a filter of
"P/E max 25" would exclude a company at exactly 25.

**Shipped as designed**, with the compilation split cleanly by language:
`screener/screen.py` gained `compile_ranges()` (emits inclusive DSL text,
sorted by metric name for a stable/shareable query string — verified in
`test_compile_ranges_produces_inclusive_dsl`), `metric_ranges()` (p5/p95
placeholders from the live snapshot, in-process cached 15 min), and
`METRIC_LABELS`/`METRIC_GROUPS`/`METRIC_FORMAT`/`DEFAULT_RANGE_METRICS` for
the UI. `web/static/js/ranges.js` (new, ~140 lines, no framework) mirrors
`compile_ranges()` in JS purely for live client-side preview as someone
types — the compiled string is the only thing that ever reaches the server,
written into the *same* `#q` field a hand-typed query uses, so `/screener`
and `/results` needed zero route changes and the existing injection tests
cover it unchanged. `compile_query` itself: untouched. NULL-exclusion (item
4) was already correct via ordinary SQL ternary logic (`NULL >= 15` is
neither true nor false, so the row drops out) — added
`test_range_filter_excludes_null_not_zero` so that can't regress silently.
Verified end-to-end in a real browser: range grid → compiled query → Advanced
text view → results, and example chips (which use `or`, something a range
grid can't express) correctly jump straight to the text view. 25/25
`test_pipeline.py`, 11/11 `test_web.py`.

---

## P5 — Per-share metrics break across stock splits *(correctness bug)* ✅ Done 2026-09-08

**Found by the first real ingest.** Not a UI preference — the numbers on the
company page are currently wrong for any company that has split.

Apple's diluted EPS series renders as:

```
FY2016  FY2017  FY2018  FY2019  FY2020  FY2021
  8.31    9.21    2.98    2.97    3.28    5.61
                  ↑ 4x discontinuity, no split happened between 2017 and 2018
```

**Why.** Restatement resolution takes the most recently *filed* value per
period, which is right for revenue and wrong for per-share figures. Apple split
4-for-1 in Aug 2020. FY2018 and FY2019 still appeared as comparatives in the
FY2020 10-K, so they were restated to post-split. FY2016 and FY2017 had already
dropped out of the comparative window, so their newest value is still
pre-split. The series silently mixes two bases.

Dollar metrics are unaffected — only per-share values restate on a split.

**Blast radius:** the EPS row on every company page, `eps_cagr_3y`, any
point-in-time EPS comparison spanning a split, and the `eps` field in the
screener. 11 of 65 ingested companies are affected.

### The fix is unusually clean, because we already hold the evidence

Keeping every filed version — the same property that makes point-in-time work —
means split factors can be recovered from our own `facts` table. The same
period, restated across two filings, differs by exactly the split ratio:

```
AAPL FY2012   44.15 →  6.31   =  7:1   (restated in the 2014-10-27 10-K)
AAPL FY2018   11.91 →  2.98   =  4:1   (restated in the 2020-10-30 10-K)
AMZN FY2021   64.81 →  3.24   = 20:1   (restated 2023-02-03)
NVDA FY2024   11.93 →  1.19   = 10:1   (restated 2025-02-26)
TSLA FY2021    4.90 →  1.63   =  3:1   (restated 2023-01-31)
```

27 such events recovered across 11 companies, every ratio exact, with **no
external corporate-actions feed**.

### Edits

1. New `screener/splits.py`: scan `facts` for same-period restatements of a
   per-share tag whose ratio is within 3% of a clean split ratio. Emit
   `(cik, effective_filing_date, ratio)`.
2. New `splits` table, populated on ingest.
3. `transform.py`: after resolving a per-share metric, multiply by the
   cumulative factor of every split recorded *after* that period's last filing.
   Applies to `eps_basic`, `eps_diluted`, `shares_diluted`,
   `shares_outstanding` — and nothing else.
4. **Point-in-time must use unadjusted values.** A 2019 vintage should show
   Apple's pre-split EPS, because that's what was on file. Adjustment belongs
   to the current view only.
5. Test: assert the AAPL FY2016→FY2018 series has no ratio jump above 1.5x.

### Watch out for

A genuine earnings collapse looks similar per-row. The distinguishing signal is
that a split restates the **same period** across filings; an earnings drop
changes the value **between periods**. Detect on the former only — my first
detector conflated them and flagged AMAT's 2012 profit crash as a split.

**Shipped, and it caught two real bugs of its own during verification against
live data** (805 companies, not the original 65-company sample):

1. **Duplicate detections of the same real split.** Different periods enter
   a filing's comparative window at different times, so the same real split
   could pass detection more than once at different filed dates (e.g. AAPL's
   2014 7:1 split showed up at both a 10-Q's filing date and the following
   10-K's). Left unfixed, a period filed before *both* dates would have the
   split's factor applied twice (7× → 49×). Fixed by collapsing to one row
   per distinct factor, keeping the earliest sighting.
2. **A false positive from an ordinary restatement.** A real Amazon Q2 2011
   EPS correction (unrelated to any split) landed within 3% of 40× on both
   the basic and diluted tags for that one quarter — exactly what the
   clean-ratio heuristic was built to catch. Fixed by requiring the same
   factor to be corroborated by **at least two distinct periods** in the
   same filing before trusting it; a real split restates every comparative
   period a filing carries at once, an ordinary correction touches one.

Both fixes are regression-tested (`tests/test_splits.py`,
`test_single_period_restatement_is_not_enough_to_call_it_a_split`). After
the fix, detection against the real DB recovers exactly the known public
split history with no noise: AAPL (7:1 2014, 4:1 2020), AMZN (20:1 2022),
NVDA (4:1 2021, 10:1 2024), TSLA (5:1 2020, 3:1 2022). AAPL's real diluted
EPS series is now monotonically smooth 2007→2025 with no split
discontinuity anywhere (verified against the live `fundamentals` table, not
just the fixture). New `splits` table + `screener/splits.py` + `python -m
screener.cli splits` backfill command (805 companies scanned, 108 real
split events on file). 7/7 `test_splits.py`, 108/108 full suite.

**Bonus, found while running the full suite read-only per an earlier ask:**
`tests/test_tape.py` had the same `FINNHUB_API_KEY` test-isolation gap
already fixed in `test_chart.py` (popping the key only *before* importing
`web.app`, not after — `_load_dotenv()`'s `setdefault` re-injects a real
local key on import even when popped beforehand). Applied the identical fix.

---

## P6 — Ingest discards the evidence needed to improve the tag map ✅ Done 2026-09-08

**Also found by the real ingest, and it undermines `/coverage`.**

`edgar.parse_companyfacts(doc, only_interesting=True)` filters every fact
against `INTERESTING_CONCEPTS` before storing. Confirmed against the real
database: 61 distinct concepts stored, 62 in the allowlist, **zero stored that
aren't already claimed**.

So `/coverage` can tell you *that* `gross_profit` resolves for only 58% of
companies, but never *which tag the other 42% used*, because that fact was
dropped at ingest. The page I described as "your work queue" cannot actually
produce the queue.

### Edits

1. `ingest.py`: add `--discover` storing all facts, or a `fact_concepts`
   census table recording `(cik, concept, count)` for every tag seen —
   cheap, and enough to drive the analysis without storing every value.
2. `/coverage`: for each thin metric, show the tags the *missing* companies
   carry, ranked by company count. That turns the page into a real work queue.
3. Re-run against the 20-company ingest before adding any tags — the current
   gaps may be legitimately absent rather than missed.

**Likely legitimate, not bugs:** `inventory` 68% (service companies hold none),
`gross_profit` 58% (already back-computed from revenue − COGS),
`cost_of_revenue` 77% (banks and insurers have none). Verify with the census
before touching `concepts.py`.

**Shipped as designed**, plus one refinement the doc didn't anticipate.
`edgar.census_companyfacts()` counts every raw tag in a company's
companyfacts document (allowlisted or not) from the *same* fetch ingest
already makes — no extra network calls for new ingests. New `fact_concepts`
table, wired into `ingest_company`/`ingest_bulk_zip`/`ingest_dir`, plus
`python -m screener.cli census` to backfill it for the 805 companies ingested
before this existed (re-fetches per company — the only way, since
`parse_companyfacts` never kept the non-allowlisted tags for us to build it
from after the fact). `/coverage` now has a "Missing companies carry" column.

**The refinement:** the naive version of the "which tag do missing companies
carry" query is dominated by boilerplate every 10-K reports regardless
(`Assets`, `NetIncomeLoss`, the three cash-flow classifications) — those came
back as the "top candidate" for *every* thin metric, which isn't a signal,
it's just what any two subsets of real filers have in common. Fixed by
excluding any tag reported by more than half the whole ingested universe.
After the fix, the page mostly confirms this doc's own prediction — the
worst-covered metrics (`short_term_investments` 38%, `gross_profit` 54%,
`inventory` 60%, `cost_of_revenue` 67%) don't show a clean single
replacement tag, because the absence is genuinely structural (financial-
statement shape varies by industry), not a missed mapping. That's the
honest answer the page exists to give, not a feature gap. 22/22
`test_pipeline.py` census assertions passing.

---

<!-- Append new items below this line. -->

## P7 — Daily EODHD price automation was silently failing

**Found**, not asked for, while answering "did we pull today's 20 companies"
from a prior session. The Windows Task Scheduler task set up earlier
(`us-screener-daily-price-ingest`) had run on schedule every night
(`NumberOfMissedRuns: 0`) but was terminating mid-run (`LastTaskResult
3221225786`, `STATUS_CONTROL_C_EXIT`) on most nights — likely the machine
sleeping or the session logging off while it ran under the default
InteractiveToken logon, which requires an active session for the whole run.
Net effect: only 20 of 290 waiting tickers got priced across 8 days, not the
~160 daily automation should have produced.

**Fixed:** re-registered the task with `-WakeToRun` (wakes a sleeping
machine), `-RestartCount 3 -RestartInterval 5min` (survives a transient
failure), and a 15-minute execution time limit (bounds a hang). True
"survives a full log-off" resilience needs either admin rights (S4U logon —
attempted, `Register-ScheduledTask` returned Access Denied without
elevation) or storing the Windows account password (which is out of scope
per the credential-handling rule) — flagging this as a known remaining gap,
not silently claiming full reliability.

**Also fixed:** `scripts/daily_price_ingest.ps1`'s log was unreadable —
PowerShell's `*>>` redirection operator writes UTF-16LE, mismatched against
the UTF-8 header lines, rendering as spaced-out garbage on read-back.
Switched to capturing output as objects and writing with explicit
`-Encoding utf8`, and added exit-code logging for both steps so a future
failure is diagnosable from the log alone instead of requiring Task
Scheduler's own history.

---

## P8 — Real-ingest growth, 2026-09-08 session

Not a PENDING-CHANGES item on its own, logged here for the record since it's
the data underneath everything else above:

- **Fundamentals:** +500 companies via `python -m screener.cli ingest
  --limit 500` (349 → 805 companies with real SEC facts, ~2M new fact rows).
- **Splits:** `python -m screener.cli splits` backfilled all 805 — 108 real
  split events recovered, zero false positives after the P5 fixes above.
- **Census:** `python -m screener.cli census` backfilled all 805 for the new
  P6 work queue.
- `normalize` + `snapshot` re-run on the full 805-company set with the split
  fix applied, so the live site reflects corrected EPS immediately, not just
  the next scheduled rebuild.
