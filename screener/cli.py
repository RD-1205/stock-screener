#!/usr/bin/env python3
"""
Command line for the pipeline. Run `python -m screener.cli --help`.

Typical first run:
    export SEC_USER_AGENT="Rudra Arora rudraarora120005@gmail.com"
    python -m screener.cli init
    python -m screener.cli tickers
    python -m screener.cli ingest --limit 50
    python -m screener.cli prices --limit 50
    python -m screener.cli normalize
    python -m screener.cli snapshot
    python -m screener.cli screen "roe > 15 and pe < 25 and market_cap > 1b"
"""

import argparse
import os
import sys
from datetime import date

from . import db, edgar, ingest, news, picker, prices, screen, sentiment, splits, transform


def _load_dotenv():
    """Same helper as web.app — keep CLI and server reading one .env."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
    except OSError:
        return


_load_dotenv()


def _ua():
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        sys.exit(
            "Set SEC_USER_AGENT first, e.g.\n"
            '  export SEC_USER_AGENT="Your Name you@example.com"\n'
            "The SEC returns 403 without a contact address in the header."
        )
    return ua


def cmd_init(args):
    conn = db.connect(args.db)
    db.init(conn)
    print(f"schema ready in {args.db}")


def cmd_tickers(args):
    conn = db.connect(args.db)
    rows = edgar.fetch_tickers(_ua())
    n = ingest.upsert_companies(conn, rows)
    print(f"{n:,} companies loaded")


def cmd_ingest(args):
    conn = db.connect(args.db)

    if args.zip:
        total = ingest_bulk(conn, args)
        print(f"{total:,} facts from {args.zip}")
        return
    if args.dir:
        total = ingest.ingest_dir(conn, args.dir, args.limit)
        print(f"{total:,} facts from {args.dir}")
        return

    ua = _ua()
    params = []
    q = "SELECT cik, ticker FROM companies WHERE ticker IS NOT NULL"
    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        q += f" AND ticker IN ({','.join('?' * len(wanted))})"
        params = wanted
    elif not args.refresh:
        q += " AND cik NOT IN (SELECT cik FROM ingest_log WHERE status='ok')"
    q += " ORDER BY cik"
    if args.limit and not args.tickers:
        q += f" LIMIT {int(args.limit)}"

    targets = conn.execute(q, params).fetchall()
    print(f"ingesting {len(targets):,} companies (~{len(targets)*0.25:.0f}s at SEC rate limit)")
    total = 0
    for i, row in enumerate(targets, 1):
        try:
            total += ingest.ingest_company(conn, row["cik"], ua)
        except Exception as e:                  # noqa: BLE001
            print(f"  ! {row['ticker']}: {e}")
        if i % 25 == 0:
            print(f"  {i}/{len(targets)}  {total:,} facts")
    print(f"done: {total:,} facts")


def ingest_bulk(conn, args):
    return ingest.ingest_bulk_zip(conn, args.zip, args.limit, progress=print)


def cmd_prices(args):
    conn = db.connect(args.db)

    if args.unpriced and not args.tickers:
        # Ranked, not alphabetical -- see screener/picker.py. News is best
        # effort: no key or a dead provider just means no news bonus.
        try:
            headlines = [h["headline"] for h in news.general(limit=50)]
        except Exception:                       # noqa: BLE001
            headlines = []
        picks = picker.pick(conn, limit=args.limit or 20, headlines=headlines)
        for t, name, reason in picks:
            print(f"  {t:<8} {name or '':<38} {reason}")
        tickers = [t for t, _n, _r in picks]
        if args.dry_run:
            print(f"(dry run: {len(tickers)} tickers picked, nothing fetched)")
            return
    else:
        params = []
        q = "SELECT DISTINCT ticker FROM companies WHERE ticker IS NOT NULL"
        if args.tickers:
            wanted = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
            q += f" AND ticker IN ({','.join('?' * len(wanted))})"
            params = wanted
        elif args.only_ingested:
            q += " AND cik IN (SELECT cik FROM facts)"
        q += " ORDER BY ticker"
        if args.limit and not args.tickers:
            q += f" LIMIT {int(args.limit)}"
        tickers = [r[0] for r in conn.execute(q, params)]

    fetch = prices.fetch_eodhd if args.provider == "eodhd" else prices.fetch_stooq
    total = 0
    for i, t in enumerate(tickers, 1):
        try:
            total += prices.store(conn, fetch(t))
        except Exception as e:                  # noqa: BLE001
            print(f"  ! {t}: {e}")
        if i % 25 == 0:
            print(f"  {i}/{len(tickers)}  {total:,} bars")
    print(f"{total:,} price bars")


def cmd_splits(args):
    """Backfill screener/splits.py detection for already-ingested companies.

    New ingests detect splits automatically; this is only needed once, for
    companies loaded before that wiring existed.
    """
    conn = db.connect(args.db)
    ciks = [r[0] for r in conn.execute("SELECT DISTINCT cik FROM facts")]
    n_events = splits.refresh_all(conn, ciks)
    print(f"{len(ciks):,} companies scanned, {n_events:,} split events found")


def cmd_census(args):
    """Backfill the raw-tag census (screener/edgar.census_companyfacts) for
    companies ingested before that wiring existed.

    New ingests build this for free from the document they already fetch --
    this re-fetches per company, since parse_companyfacts() never kept the
    non-allowlisted tags to build it from after the fact.
    """
    conn = db.connect(args.db)
    ua = _ua()
    ciks = [r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE cik NOT IN "
        "(SELECT DISTINCT cik FROM fact_concepts)"
    )]
    print(f"census for {len(ciks):,} companies (~{len(ciks)*0.12:.0f}s at SEC rate limit)")
    total = 0
    for i, cik in enumerate(ciks, 1):
        try:
            doc = edgar.fetch_companyfacts(cik, ua)
            total += len(ingest._store_census(conn, doc))
        except Exception as e:                  # noqa: BLE001
            print(f"  ! cik {cik}: {e}")
        if i % 25 == 0:
            conn.commit()
            print(f"  {i}/{len(ciks)}")
    conn.commit()
    print(f"census built for {len(ciks):,} companies, {total:,} tag rows")


def cmd_normalize(args):
    conn = db.connect(args.db)
    n = transform.normalize_all(conn)
    print(f"{n:,} normalized fundamental rows")


def cmd_snapshot(args):
    conn = db.connect(args.db)
    n = transform.build_snapshot(conn)
    print(f"snapshot rebuilt for {n:,} companies")


def cmd_sentiment(args):
    conn = db.connect(args.db)
    row = sentiment.compute(conn)
    if not row:
        print(f"not enough priced companies yet (need >= {sentiment.MIN_UNIVERSE})")
        return
    sentiment.store(conn, row)
    zslug, zlabel = sentiment.zone_for(row["composite"])
    print(f"{row['date']}  composite={row['composite']:.1f} ({zlabel})  "
         f"momentum={row['momentum']}  strength={row['strength']}  "
         f"breadth={row['breadth']}  universe={row['universe_size']}")


def cmd_history(args):
    """Build point-in-time snapshots at quarter ends."""
    conn = db.connect(args.db)
    end = args.to or date.today().isoformat()
    dates = transform.month_ends(args.since, end)
    if not dates:
        sys.exit(f"no quarter ends between {args.since} and {end}")
    print(f"building {len(dates)} vintages ({dates[0]} .. {dates[-1]})")
    transform.build_history(conn, dates, progress=print)
    print(f"done: {len(dates)} vintages in snapshot_history")


def cmd_screen(args):
    conn = db.connect(args.db)
    try:
        rows = screen.run(conn, args.query, order_by=args.order,
                          desc=not args.asc, limit=args.limit,
                          as_of=args.as_of)
    except screen.QueryError as e:
        sys.exit(f"query error: {e}")
    print(screen.format_table(rows))
    if args.as_of:
        print(f"\n{len(rows)} rows as of {args.as_of} "
              f"(only filings that existed on that date)")
    else:
        print(f"\n{len(rows)} rows")


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("pip install fastapi uvicorn jinja2")
    os.environ["SCREENER_DB"] = os.path.abspath(args.db)
    print(f"serving {os.environ['SCREENER_DB']} on http://{args.host}:{args.port}")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_demo(args):
    """Seed a fully browsable database from fixtures -- no network needed."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.make_fixtures import write_all, seed_db

    conn = db.connect(args.db)
    db.init(conn)
    fixtures = os.path.join(os.path.dirname(os.path.abspath(args.db)), "_fixtures")
    write_all(fixtures)
    summary = seed_db(conn, fixtures)
    print("seeded:", ", ".join(f"{k}={v:,}" for k, v in summary.items()))

    dates = transform.month_ends("2021-06-30", "2025-06-30")
    transform.build_history(conn, dates)
    print(f"built {len(dates)} point-in-time vintages")
    print(f"\nnow run:  python -m screener.cli --db {args.db} serve")


def cmd_company(args):
    conn = db.connect(args.db)
    row = conn.execute(
        "SELECT * FROM snapshot WHERE ticker = ?", (args.ticker.upper(),)
    ).fetchone()
    if not row:
        sys.exit(f"{args.ticker} not in snapshot")
    for k in row.keys():
        print(f"{k:>22}: {screen._fmt(row[k])}")

    print("\nAnnual history")
    hist = conn.execute(
        "SELECT period_end, metric, val FROM fundamentals "
        "WHERE cik=? AND period_type='FY' AND metric IN "
        "('revenue','operating_income','net_income','eps_diluted') "
        "ORDER BY period_end DESC LIMIT 40",
        (row["cik"],),
    ).fetchall()
    by_year = {}
    for h in hist:
        by_year.setdefault(h["period_end"][:4], {})[h["metric"]] = h["val"]
    for year in sorted(by_year, reverse=True):
        vals = by_year[year]
        print(f"  {year}  " + "  ".join(
            f"{m}={screen._fmt(vals.get(m))}"
            for m in ("revenue", "operating_income", "net_income", "eps_diluted")
        ))


def main(argv=None):
    p = argparse.ArgumentParser(prog="screener", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.environ.get("SCREENER_DB", "screener.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("tickers").set_defaults(func=cmd_tickers)

    g = sub.add_parser("ingest", help="load XBRL facts")
    g.add_argument("--limit", type=int)
    g.add_argument("--tickers", help="comma-separated tickers, e.g. AAPL,MSFT (overrides --limit)")
    g.add_argument("--refresh", action="store_true", help="re-fetch already-ingested filers")
    g.add_argument("--zip", help="path to SEC companyfacts.zip (bulk load)")
    g.add_argument("--dir", help="directory of companyfacts JSON files")
    g.set_defaults(func=cmd_ingest)

    g = sub.add_parser("prices")
    g.add_argument("--limit", type=int)
    g.add_argument("--tickers", help="comma-separated tickers, e.g. AAPL,MSFT (overrides --limit)")
    g.add_argument("--provider", choices=["stooq", "eodhd"], default="stooq")
    g.add_argument("--only-ingested", action="store_true", default=True)
    g.add_argument("--unpriced", action="store_true",
                    help="only tickers with no prices yet, ranked by size and "
                         "news (not alphabetical)")
    g.add_argument("--dry-run", action="store_true",
                    help="with --unpriced: show the picks, fetch nothing")
    g.set_defaults(func=cmd_prices)

    sub.add_parser("splits", help="backfill split detection for already-ingested companies") \
       .set_defaults(func=cmd_splits)
    sub.add_parser("census", help="backfill the raw-tag census for /coverage's work queue") \
       .set_defaults(func=cmd_census)
    sub.add_parser("normalize").set_defaults(func=cmd_normalize)
    sub.add_parser("snapshot").set_defaults(func=cmd_snapshot)
    sub.add_parser("sentiment", help="compute + store today's market mood reading") \
       .set_defaults(func=cmd_sentiment)

    g = sub.add_parser("history", help="build point-in-time vintages")
    g.add_argument("--since", default="2015-01-01")
    g.add_argument("--to", default=None)
    g.set_defaults(func=cmd_history)

    g = sub.add_parser("screen")
    g.add_argument("query", nargs="?", default="")
    g.add_argument("--order", default="market_cap")
    g.add_argument("--asc", action="store_true")
    g.add_argument("--limit", type=int, default=50)
    g.add_argument("--as-of", dest="as_of", default=None,
                   help="replay against a historical vintage, YYYY-MM-DD")
    g.set_defaults(func=cmd_screen)

    g = sub.add_parser("company")
    g.add_argument("ticker")
    g.set_defaults(func=cmd_company)

    g = sub.add_parser("serve", help="run the web app")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8000)
    g.add_argument("--reload", action="store_true")
    g.set_defaults(func=cmd_serve)

    sub.add_parser(
        "demo", help="seed a browsable DB from fixtures, no network"
    ).set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
