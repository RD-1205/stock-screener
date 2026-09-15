"""
Recovers stock-split ratios from same-period restatements already sitting in
`facts` -- no external corporate-actions feed needed.

Why this works: when a company splits, EDGAR restates every comparative
period a *later* filing still shows to the new share basis. But a 10-K only
carries 2-3 years of comparatives, so a period old enough to have already
aged out of that window before the split happened keeps its ORIGINAL,
pre-split value in `facts` forever -- while a period still inside the window
gets silently rewritten. `normalize_company` always keeps the latest-filed
version per period, so the resulting series quietly mixes two share bases
(see docs/PENDING-CHANGES.md P5). The same period, reported both before and
after the split, differs by exactly the split ratio -- we already store
every filed version, so the ratio is just sitting there.

Detection is scoped to EPS tags only (not share-count tags): a weighted
average share count also drifts a few percent from ordinary buybacks and
issuance, which is exactly the kind of noise the clean-ratio tolerance below
would otherwise have to absorb from two different sources at once.
"""

EPS_CONCEPTS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "EarningsPerShareBasicAndDiluted",
)

# Splits are announced in round ratios. 3% tolerance absorbs ordinary
# restatement noise without matching a genuine ~Nx earnings swing between
# unrelated periods.
CLEAN_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50)
TOLERANCE = 0.03


def _nearest_clean_ratio(magnitude):
    best = min(CLEAN_RATIOS, key=lambda r: abs(magnitude - r))
    if abs(magnitude - best) / best <= TOLERANCE:
        return best
    return None


def detect_splits(conn, cik):
    """Find this company's split events from EPS restatements in `facts`.

    Returns [(effective_filed, factor), ...] sorted by date. `factor` is the
    share multiplier: dividing a pre-split EPS by it, or multiplying a
    pre-split share count by it, lands on the current (post-split) basis.
    Forward splits give factor > 1; reverse splits give factor < 1.

    A single restated period is not enough to call it a split -- an ordinary
    one-off correction to a prior figure can coincidentally land near a
    clean ratio too (found on real EDGAR data: a genuine EPS restatement
    landed within 3% of 40x on both the basic and diluted tags for one
    quarter). A real split restates *every* comparative period a filing
    carries at once, so require the same factor to show up from at least
    two distinct periods in the same filing before trusting it. Basic and
    diluted of the *same* period don't count as two -- they restate in
    lockstep for ordinary reasons too, not just splits.
    """
    rows = conn.execute(
        "SELECT concept, period_start, period_end, val, filed FROM facts "
        "WHERE cik=? AND concept IN (?,?,?) AND val IS NOT NULL AND val > 0 "
        "ORDER BY period_start, period_end, filed ASC",
        (cik,) + EPS_CONCEPTS,
    ).fetchall()

    by_period = {}
    for r in rows:
        key = (r["concept"], r["period_start"], r["period_end"])
        by_period.setdefault(key, []).append(r)

    # filed date -> {factor: set of corroborating (period_start, period_end)}
    candidates = {}
    for (concept, pstart, pend), versions in by_period.items():
        for prev, cur in zip(versions, versions[1:]):
            if cur["val"] == prev["val"]:
                continue
            raw = prev["val"] / cur["val"]
            magnitude, invert = (raw, False) if raw > 1 else (1.0 / raw, True)
            clean = _nearest_clean_ratio(magnitude)
            if clean is None:
                continue
            factor = (1.0 / clean) if invert else float(clean)
            periods = candidates.setdefault(cur["filed"], {}).setdefault(factor, set())
            periods.add((pstart, pend))

    hits = []
    for filed, by_factor in candidates.items():
        factor, periods = max(by_factor.items(), key=lambda kv: len(kv[1]))
        if len(periods) >= 2:
            hits.append((filed, factor))
    hits.sort()

    # Different periods can enter a filing's comparative window at slightly
    # different times, so the *same* real split can pass the corroboration
    # check more than once (e.g. a 10-Q's window catches it a quarter before
    # the next 10-K's does). Collapse to one row per distinct factor -- the
    # earliest sighting -- so `factor_after` never multiplies one real split
    # in twice for a period filed before both duplicate dates.
    earliest_by_factor = {}
    for filed, factor in hits:
        earliest_by_factor.setdefault(factor, filed)

    return sorted((filed, factor) for factor, filed in earliest_by_factor.items())


def store_splits(conn, cik, events):
    conn.execute("DELETE FROM splits WHERE cik=?", (cik,))
    if events:
        conn.executemany(
            "INSERT INTO splits (cik, effective_filed, factor) VALUES (?,?,?)",
            [(cik, filed, factor) for filed, factor in events],
        )


def refresh_splits(conn, cik):
    """Detect and (re)store split events for one company. Returns the count."""
    events = detect_splits(conn, cik)
    store_splits(conn, cik, events)
    return len(events)


def refresh_all(conn, ciks=None):
    if ciks is None:
        ciks = [r[0] for r in conn.execute("SELECT DISTINCT cik FROM facts")]
    total = 0
    for cik in ciks:
        total += refresh_splits(conn, cik)
    conn.commit()
    return total


def load_splits(conn, cik):
    """This company's stored split events as [(effective_filed, factor), ...]."""
    return [
        (r["effective_filed"], r["factor"])
        for r in conn.execute(
            "SELECT effective_filed, factor FROM splits WHERE cik=? "
            "ORDER BY effective_filed", (cik,),
        )
    ]


def factor_after(split_events, filed):
    """Cumulative factor of every split effective strictly after `filed`.

    A period whose resolved value was itself filed on/after a split's
    effective date already reflects that split (either the filing that
    restated it, or a later one) -- only strictly-later splits still need
    to be applied to bring an unadjusted value to the current basis.
    """
    factor = 1.0
    for effective_filed, f in split_events:
        if effective_filed > filed:
            factor *= f
    return factor
