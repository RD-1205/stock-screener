# Pending changes

Queued edits to apply in one batch. Nothing here is implemented yet.

Add new items to the bottom. Each entry records what Rudra asked for, what I'd
actually do, and anything that would break — so the batch can be applied
without re-litigating the reasoning.

---

## P1 — Remove alphabetical *sorting* (keep the letter *filter*)

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

---

## P2 — Collapse the browse filters behind a Filters button

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

## P4 — Screener: range filters instead of typed comparisons

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

---

<!-- Append new items below this line. -->
