"""
Static page copy.

Kept in Python rather than a CMS because it changes rarely and a database
round-trip for terms of service is silly. Move to markdown files if these grow.

NOT LEGAL ADVICE. These are honest working drafts that describe what the site
actually does — enough to launch a free product, and a sane starting point to
hand a lawyer before taking money. See docs/PRODUCT-PLAN.md §2.6.
"""

from datetime import date

UPDATED = "August 2026"

DISCLAIMER = """
<h2>Not investment advice</h2>
<p>Everything on this site is information and tooling, not a recommendation.
We do not know your circumstances, goals or risk tolerance, and nothing here
should be read as a suggestion to buy, sell or hold any security.</p>

<h2>The data will sometimes be wrong</h2>
<p>Fundamentals are parsed automatically from SEC EDGAR XBRL filings. That
process involves judgement calls — which of ~14,000 tags a company used for a
given concept, how to derive a fourth quarter that was never separately filed,
how to difference year-to-date cash flow into discrete quarters. We document
these choices on the <a href="/methodology">methodology page</a> and show the
source tag behind every figure, but automated parsing produces errors.</p>
<p><strong>Verify against the primary filing before acting on anything.</strong>
Every figure links back to its filing on EDGAR for exactly this reason.</p>

<h2>Prices are delayed</h2>
<p>Price data is end-of-day or delayed by at least 15 minutes. It is not
suitable for trading decisions that depend on current quotes.</p>

<h2>Historical and backtested results</h2>
<p>Point-in-time screening reconstructs what filings existed on a past date. It
is a research tool, not a performance record. Backtested results ignore
transaction costs, slippage, taxes and market impact, and a screen that
performed well historically may perform badly in future. Past performance does
not predict future results.</p>

<h2>No relationship</h2>
<p>Using this site does not create an advisory or fiduciary relationship. We
are not a registered investment adviser, broker-dealer or financial planner.</p>
"""

TERMS = """
<h2>1. Acceptance</h2>
<p>By using this site you agree to these terms. If you do not agree, please
don't use it.</p>

<h2>2. What the service is</h2>
<p>A stock screening and research tool built on public SEC filings. It is
provided on an "as is" and "as available" basis, with no warranty of accuracy,
completeness, timeliness or fitness for any purpose.</p>

<h2>3. Acceptable use</h2>
<p>You may use the site for personal and commercial research. You may not
scrape it at a rate that degrades service for others, resell bulk data, attempt
to breach access controls, or misrepresent the source of the data.</p>

<h2>4. Data rights</h2>
<p>Fundamental data originates from SEC EDGAR and is a work of the United
States government, in the public domain. Our normalization, derived metrics and
presentation are ours. Price data is licensed from third parties and subject to
their terms — it may not be redistributed in bulk.</p>

<h2>5. Accounts</h2>
<p>You are responsible for activity under your account and for keeping your
credentials secure. We may suspend accounts that breach these terms.</p>

<h2>6. Limitation of liability</h2>
<p>To the maximum extent permitted by law, we are not liable for any trading
loss, lost profit, or indirect or consequential damage arising from use of this
site. Any liability is limited to the amount you have paid us in the preceding
twelve months.</p>

<h2>7. Changes</h2>
<p>These terms may change. Material changes will be noted on this page with an
updated date, and on the <a href="/changelog">changelog</a>.</p>
"""

PRIVACY = """
<h2>What we collect</h2>
<ul>
<li><strong>Nothing, if you just browse.</strong> No account is required to
search, screen or read company pages.</li>
<li><strong>Local storage.</strong> Your theme, density, motion preference and
anonymous watchlist are stored in your browser, not on our servers.</li>
<li><strong>With an account.</strong> Email address, saved screens, watchlists
and alert settings.</li>
<li><strong>Analytics.</strong> Aggregate, privacy-preserving page counts with
no cookies and no cross-site tracking.</li>
</ul>

<h2>What we don't do</h2>
<p>No advertising trackers. No selling or sharing of personal data. No
third-party cookies.</p>

<h2>Third parties</h2>
<p>Market data providers, payment processing (if you subscribe) and error
monitoring. Each receives only what it needs to function.</p>

<h2>Your rights</h2>
<p>Export or delete your data at any time from account settings, or by
contacting us. Deleting your account removes saved screens, watchlists and
alerts permanently.</p>

<h2>Contact</h2>
<p>Questions about privacy: see the <a href="/about">about page</a>.</p>
"""

METHODOLOGY = """
<h2>Where the numbers come from</h2>
<p>Every fundamental figure on this site is parsed from a company's own XBRL
filings on SEC EDGAR. Nothing is bought from a data vendor, and nothing is
hand-entered. Hover any figure to see the exact <code>us-gaap</code> tag it was
resolved from and click through to the filing itself.</p>

<h2>Why normalization is necessary</h2>
<p>XBRL standardizes the <em>format</em> of filings, not the <em>vocabulary</em>.
The SEC lets companies choose from roughly 14,000 tags, and there is no rule
forcing two companies in the same industry to pick the same one. Apple reports
revenue as <code>RevenueFromContractWithCustomerExcludingAssessedTax</code>;
other filers use <code>Revenues</code>, and older filings use the now-deprecated
<code>SalesRevenueNet</code>. Screening on a single tag silently returns nothing
for a large share of the market.</p>
<p>We maintain a mapping from candidate tags to canonical metrics, resolved in
priority order. It is imperfect and improving; the
<a href="/coverage">coverage page</a> shows exactly how much of the universe
resolves for each metric.</p>

<h2>Three derivations worth knowing about</h2>
<h3>There is no fourth quarter</h3>
<p>US companies file three 10-Qs and one 10-K. No Q4 income statement exists
anywhere in EDGAR. We derive it as the fiscal year minus the nine-month
year-to-date figure. Summing "the last four quarters" without this step
produces a trailing-twelve-month number that is roughly a quarter short.</p>

<h3>Cash flow is filed year-to-date</h3>
<p>A 10-Q's cash flow statement covers six or nine months, not the quarter. We
recover discrete quarters by differencing consecutive year-to-date periods that
share a fiscal-year start.</p>

<h3>Restatements</h3>
<p>The same quarter is reported many times — originally, restated in the 10-K,
and again as a comparative the following year. We keep every version with its
filing date, resolve to the most recent for current figures, and use the
filing date to reconstruct history accurately.</p>

<h2>Point-in-time</h2>
<p>Screening "as of" a past date filters to filings that existed on that date —
by <em>filing</em> date, not period end. A fiscal-2023 annual report is filed in
early 2024, so nobody screening in January 2024 could see it. Prices use the
last close on or before the date, and the universe excludes companies that
hadn't filed yet.</p>

<h2>Known limitations</h2>
<p>Stated plainly, because every one of these affects how you should read the
numbers:</p>
<ul>
<li>XBRL was phased in around 2009–2011. Coverage degrades before then.</li>
<li>Banks and insurers have no meaningful gross margin, and several ratios are
not comparable across industries.</li>
<li>Fiscal years are not calendar years. "FY2024" covers different periods for
different companies.</li>
<li>Earnings are as-reported. We do not compute adjusted or non-GAAP figures.</li>
<li>Institutional holdings come from 13F filings, which lag 45 days, cover only
managers above $100M, and show long US equity positions only.</li>
<li>Prices are end-of-day or delayed.</li>
</ul>

<h2>When a figure is blank</h2>
<p>A dash means we could not compute the value honestly, not that it is zero.
Return on equity is left blank for companies with negative equity, price to
earnings for companies losing money, and gross margin where no cost of revenue
was ever tagged. Showing a number in those cases would invite you to sort by it
and find nonsense.</p>
"""

ABOUT = """
<h2>What this is</h2>
<p>A US equity screener built directly on SEC filings, with one feature no free
screener offers: the filing dates are kept, so any screen can be replayed
against a past date using only the information that actually existed then.</p>

<h2>Why it exists</h2>
<p>Every free screener rebuilds history from today's data. Run a backtest on one
and it sees figures that were later restated, and companies that had not yet
listed. The results look better than reality, and it cannot be fixed
retroactively — you need the filing date on every datapoint from the start.</p>

<h2>How it's built</h2>
<p>A Python pipeline ingests XBRL from EDGAR, normalizes inconsistent tags into
one schema, derives the periods companies never file, and stores every version
of every figure. The site is server-rendered so pages are fast and readable
without JavaScript.</p>
<p>The <a href="/methodology">methodology page</a> documents the parsing
decisions, and the <a href="/coverage">coverage page</a> shows how complete the
data currently is — including where it isn't.</p>
"""

PAGES = {
    "disclaimer": ("Disclaimer", "Important limitations of the data and tools on this site.", DISCLAIMER),
    "terms": ("Terms of service", "Terms governing use of this site.", TERMS),
    "privacy": ("Privacy", "What we collect, and what we don't.", PRIVACY),
    "methodology": ("Methodology", "Where the data comes from, how it's normalized, and its known limitations.", METHODOLOGY),
    "about": ("About", "Why this exists and how it's built.", ABOUT),
}


def get(slug):
    """Returns (title, description, html, updated) or None."""
    page = PAGES.get(slug)
    if not page:
        return None
    return page[0], page[1], page[2], UPDATED
