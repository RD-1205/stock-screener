-- US equity screener schema.
-- Written for SQLite (zero setup) but deliberately Postgres-portable:
-- no SQLite-only types, no AUTOINCREMENT, explicit PKs everywhere.
-- To move to Postgres: REAL -> DOUBLE PRECISION, TEXT dates -> DATE, done.

-- ---------------------------------------------------------------
-- 1. Reference data
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    cik              INTEGER PRIMARY KEY,
    ticker           TEXT,
    name             TEXT NOT NULL,
    sic              TEXT,
    sic_description  TEXT,
    exchange         TEXT,
    fiscal_year_end  TEXT,          -- 'MMDD'
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);


-- ---------------------------------------------------------------
-- 2. Raw XBRL facts, exactly as SEC reported them.
--
-- One row per (company, concept, unit, period, filing). We keep the
-- accession number in the PK because the SAME period gets reported
-- many times: original 10-Q, then restated in the 10-K, then again in
-- next year's comparative column. Storing all of them and resolving
-- later is the right call -- it lets you answer "what did they say at
-- the time?" vs "what is true now?", and restatement history is
-- genuinely interesting signal.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    cik           INTEGER NOT NULL,
    taxonomy      TEXT    NOT NULL,   -- 'us-gaap' | 'dei' | 'ifrs-full'
    concept       TEXT    NOT NULL,   -- e.g. 'NetIncomeLoss'
    unit          TEXT    NOT NULL,   -- 'USD' | 'shares' | 'USD/shares'
    period_start  TEXT    NOT NULL,   -- '' for instant (point-in-time) facts
    period_end    TEXT    NOT NULL,
    fy            INTEGER,
    fp            TEXT,               -- 'FY' | 'Q1' | 'Q2' | 'Q3'
    form          TEXT,               -- '10-K' | '10-Q' | '8-K' ...
    filed         TEXT    NOT NULL,
    accn          TEXT    NOT NULL,
    frame         TEXT,               -- SEC's calendar-aligned label, e.g. 'CY2023Q1'
    val           REAL,
    PRIMARY KEY (cik, taxonomy, concept, unit, period_start, period_end, accn)
);
CREATE INDEX IF NOT EXISTS idx_facts_concept ON facts(concept, period_end);
CREATE INDEX IF NOT EXISTS idx_facts_cik     ON facts(cik, concept);


-- ---------------------------------------------------------------
-- 2b. Recovered stock splits (see docs/PENDING-CHANGES.md P5).
--
-- The same period restates across filings by exactly the split ratio, so
-- these are recovered from `facts` itself (screener/splits.py), never
-- entered by hand or pulled from an external corporate-actions feed.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS splits (
    cik              INTEGER NOT NULL,
    effective_filed  TEXT    NOT NULL,   -- filing date the restated value first appeared
    factor           REAL    NOT NULL,   -- share multiplier: >1 forward split, <1 reverse
    PRIMARY KEY (cik, effective_filed)
);
CREATE INDEX IF NOT EXISTS idx_splits_cik ON splits(cik);


-- ---------------------------------------------------------------
-- 2c. Raw tag census -- every concept a company reports, not just the
-- ones concepts.py already claims (see docs/PENDING-CHANGES.md P6).
--
-- `facts` only ever holds allowlisted concepts (parse_companyfacts filters
-- before storing, on purpose -- storing every value for every one of the
-- ~14,000 us-gaap tags across the whole market would be enormous for no
-- benefit). But that means /coverage can say a metric is thin without ever
-- being able to say which tag the missing companies used instead. This
-- table is the cheap middle ground: one row per (company, tag) with just a
-- count, built from the same companyfacts document ingest already fetches.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_concepts (
    cik       INTEGER NOT NULL,
    taxonomy  TEXT    NOT NULL,
    concept   TEXT    NOT NULL,
    n_facts   INTEGER NOT NULL,
    PRIMARY KEY (cik, taxonomy, concept)
);
CREATE INDEX IF NOT EXISTS idx_fact_concepts_concept ON fact_concepts(concept);


-- ---------------------------------------------------------------
-- 3. Normalized fundamentals.
--
-- This is the layer that makes a screener a screener. `facts` is
-- unusable for cross-company comparison because Apple tags revenue as
-- RevenueFromContractWithCustomerExcludingAssessedTax while an older
-- filer uses SalesRevenueNet and a third uses Revenues. Here every
-- company speaks the same vocabulary.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fundamentals (
    cik             INTEGER NOT NULL,
    metric          TEXT    NOT NULL,   -- canonical name, see concepts.py
    period_end      TEXT    NOT NULL,
    period_type     TEXT    NOT NULL,   -- 'FY' | 'Q' | 'INSTANT'
    val             REAL,
    source_concept  TEXT,               -- which raw tag this came from (audit trail)
    derived         INTEGER NOT NULL DEFAULT 0,  -- 1 = computed, not directly reported
    filed           TEXT,
    PRIMARY KEY (cik, metric, period_end, period_type)
);
CREATE INDEX IF NOT EXISTS idx_fund_metric ON fundamentals(metric, period_end);


-- ---------------------------------------------------------------
-- 4. Prices (end of day)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prices (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    PRIMARY KEY (ticker, date)
);


-- ---------------------------------------------------------------
-- 5. Screening snapshot -- wide, denormalized, one row per company.
--
-- Rebuilt nightly from the tables above. Screeners are read-heavy with
-- filters across many columns at once; joining `fundamentals` at query
-- time to answer "PE < 15 AND ROE > 20 AND debt/equity < 0.5" means 3
-- self-joins over millions of rows. A flat table makes it one seq scan.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot (
    cik                INTEGER PRIMARY KEY,
    ticker             TEXT,
    name               TEXT,
    sic_description    TEXT,
    as_of              TEXT,

    price              REAL,
    market_cap         REAL,
    shares_diluted     REAL,

    -- trailing twelve months (flows)
    revenue_ttm        REAL,
    gross_profit_ttm   REAL,
    operating_income_ttm REAL,
    net_income_ttm     REAL,
    eps_ttm            REAL,
    ocf_ttm            REAL,
    capex_ttm          REAL,
    fcf_ttm            REAL,

    -- latest balance sheet (stocks)
    total_assets       REAL,
    total_equity       REAL,
    total_debt         REAL,
    cash               REAL,

    -- valuation
    pe                 REAL,
    pb                 REAL,
    ps                 REAL,
    ev                 REAL,
    ev_to_ebit         REAL,

    -- quality
    roe                REAL,
    roa                REAL,
    gross_margin       REAL,
    operating_margin   REAL,
    net_margin         REAL,
    debt_to_equity     REAL,
    current_ratio      REAL,

    -- growth
    revenue_cagr_3y    REAL,
    eps_cagr_3y        REAL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_pe  ON snapshot(pe);
CREATE INDEX IF NOT EXISTS idx_snapshot_mc  ON snapshot(market_cap);


-- ---------------------------------------------------------------
-- 5b. Point-in-time snapshots.
--
-- Same columns as `snapshot`, plus the vintage date. A row here answers
-- "what did this company look like to someone screening on 2019-03-31,
-- using only filings that existed on 2019-03-31?"
--
-- Every free screener silently fails this. They rebuild history from
-- today's data, so a 2019 backtest sees restated figures nobody had in
-- 2019, and screens on companies that hadn't reported yet. That is
-- lookahead bias, it makes every backtest optimistic, and it cannot be
-- retrofitted -- you need the `filed` date on every fact, from day one.
-- We have it, so this is just careful querying.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot_history (
    as_of              TEXT    NOT NULL,
    cik                INTEGER NOT NULL,
    ticker             TEXT,
    name               TEXT,
    sic_description    TEXT,

    price              REAL,
    market_cap         REAL,
    shares_diluted     REAL,

    revenue_ttm        REAL,
    gross_profit_ttm   REAL,
    operating_income_ttm REAL,
    net_income_ttm     REAL,
    eps_ttm            REAL,
    ocf_ttm            REAL,
    capex_ttm          REAL,
    fcf_ttm            REAL,

    total_assets       REAL,
    total_equity       REAL,
    total_debt         REAL,
    cash               REAL,

    pe                 REAL,
    pb                 REAL,
    ps                 REAL,
    ev                 REAL,
    ev_to_ebit         REAL,

    roe                REAL,
    roa                REAL,
    gross_margin       REAL,
    operating_margin   REAL,
    net_margin         REAL,
    debt_to_equity     REAL,
    current_ratio      REAL,

    revenue_cagr_3y    REAL,
    eps_cagr_3y        REAL,

    -- how stale was the newest filing at this vintage? high values mean
    -- the company was between reports and the numbers were months old.
    data_age_days      INTEGER,

    PRIMARY KEY (as_of, cik)
);
CREATE INDEX IF NOT EXISTS idx_hist_asof ON snapshot_history(as_of);
CREATE INDEX IF NOT EXISTS idx_hist_cik  ON snapshot_history(cik, as_of);


-- Scratch table: normalized fundamentals for one vintage, rebuilt and
-- truncated per as_of date. Same shape as `fundamentals`.
CREATE TABLE IF NOT EXISTS fundamentals_pit (
    cik             INTEGER NOT NULL,
    metric          TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    period_type     TEXT    NOT NULL,
    val             REAL,
    source_concept  TEXT,
    derived         INTEGER NOT NULL DEFAULT 0,
    filed           TEXT,
    PRIMARY KEY (cik, metric, period_end, period_type)
);


-- ---------------------------------------------------------------
-- 6. Ingestion bookkeeping -- lets you resume a 10,000-company crawl
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_log (
    cik         INTEGER PRIMARY KEY,
    last_run    TEXT,
    status      TEXT,     -- 'ok' | 'error' | 'empty'
    fact_count  INTEGER,
    message     TEXT
);


-- ---------------------------------------------------------------
-- 7. Index membership.
--
-- There is no free, authoritative, machine-readable S&P 500 constituent list
-- -- S&P licenses it. The practical options are a maintained CSV in the repo
-- or a paid feed. We keep a CSV and refresh it periodically; `as_of` records
-- when, so a stale list is visible rather than silently wrong.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS index_members (
    index_slug TEXT NOT NULL,       -- 'sp500', 'nasdaq100', 'dow30'
    index_name TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    as_of      TEXT,
    PRIMARY KEY (index_slug, ticker)
);
CREATE INDEX IF NOT EXISTS idx_index_ticker ON index_members(ticker);


-- ---------------------------------------------------------------
-- 8. Market sentiment gauge.
--
-- CNN's Fear & Greed Index weights 7 indicators equally, each as a
-- standard-deviation move from its own recent norm (see
-- docs/DESIGN-SPEC.md 7.3). v1 computes the 3 that come straight from our
-- own price DB -- momentum, price strength, breadth -- honestly labelled as
-- partial rather than faking the other 4 (volatility, junk bond demand,
-- safe haven demand, put/call), which need FRED/CBOE feeds not wired up yet.
-- One row per trading day so a real history (and eventually proper z-scores
-- against it, once enough days exist) accumulates starting from day one.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sentiment (
    date            TEXT PRIMARY KEY,   -- YYYY-MM-DD, the trading day this reflects
    momentum        REAL,               -- 0-100: universe price vs its own moving average
    strength        REAL,               -- 0-100: net share near highs vs near lows
    breadth         REAL,               -- 0-100: advancing vs declining volume
    composite       REAL,               -- equal-weighted average of the components above
    components_json TEXT,               -- raw inputs behind each score, for the breakdown UI
    universe_size   INTEGER,            -- how many priced tickers fed this reading
    computed_at     TEXT
);
