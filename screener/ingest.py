"""Loading facts into the DB, from the live API or from the nightly bulk zip."""

import json
import os
import zipfile
from datetime import datetime

from . import edgar

FACT_INSERT = (
    "INSERT OR REPLACE INTO facts "
    "(cik,taxonomy,concept,unit,period_start,period_end,fy,fp,form,filed,accn,frame,val) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def upsert_companies(conn, rows):
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO companies (cik,ticker,name,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker, "
        "name=excluded.name, updated_at=excluded.updated_at",
        [(r["cik"], r["ticker"], r["name"], now) for r in rows],
    )
    conn.commit()
    return len(rows)


def _log(conn, cik, status, count, message=""):
    conn.execute(
        "INSERT OR REPLACE INTO ingest_log (cik,last_run,status,fact_count,message) "
        "VALUES (?,?,?,?,?)",
        (cik, datetime.utcnow().isoformat(timespec="seconds"), status, count, message),
    )


def ingest_company(conn, cik, user_agent, with_meta=True):
    """Fetch one company's facts from the live API."""
    try:
        if with_meta:
            meta = edgar.company_meta_from_submissions(
                edgar.fetch_submissions(cik, user_agent)
            )
            if meta:
                conn.execute(
                    "INSERT INTO companies (cik,ticker,name,sic,sic_description,"
                    "exchange,fiscal_year_end,updated_at) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(cik) DO UPDATE SET sic=excluded.sic, "
                    "sic_description=excluded.sic_description, exchange=excluded.exchange, "
                    "fiscal_year_end=excluded.fiscal_year_end",
                    (meta["cik"], meta["ticker"], meta["name"], meta["sic"],
                     meta["sic_description"], meta["exchange"],
                     meta["fiscal_year_end"],
                     datetime.utcnow().isoformat(timespec="seconds")),
                )

        doc = edgar.fetch_companyfacts(cik, user_agent)
        rows = edgar.parse_companyfacts(doc)
        if rows:
            conn.executemany(FACT_INSERT, rows)
        _log(conn, cik, "ok" if rows else "empty", len(rows))
        conn.commit()
        return len(rows)
    except Exception as e:                      # noqa: BLE001
        # One bad filer must never kill a 10,000-company crawl.
        _log(conn, cik, "error", 0, str(e)[:400])
        conn.commit()
        raise


def ingest_bulk_zip(conn, zip_path, limit=None, progress=None):
    """Parse the SEC's companyfacts.zip -- 10k companies, one download.

    This is the right way to do a full-market load. Hitting the per-company
    endpoint 10,000 times at 10 req/s takes ~17 minutes of continuous
    hammering; the zip takes one request.
    """
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
        if limit:
            names = names[:limit]
        for i, name in enumerate(names, 1):
            try:
                with zf.open(name) as fh:
                    doc = json.load(fh)
                rows = edgar.parse_companyfacts(doc)
                if rows:
                    conn.executemany(FACT_INSERT, rows)
                    total += len(rows)
                _log(conn, int(doc.get("cik", 0)), "ok" if rows else "empty", len(rows))
            except Exception as e:              # noqa: BLE001
                if progress:
                    progress(f"  skip {name}: {e}")
            if i % 200 == 0:
                conn.commit()
                if progress:
                    progress(f"  {i}/{len(names)} files, {total:,} facts")
    conn.commit()
    return total


def ingest_dir(conn, path, limit=None):
    """Parse a directory of companyfacts JSON files (unzipped bulk, or fixtures)."""
    files = sorted(f for f in os.listdir(path) if f.endswith(".json"))
    if limit:
        files = files[:limit]
    total = 0
    for fname in files:
        with open(os.path.join(path, fname), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        rows = edgar.parse_companyfacts(doc)
        if rows:
            conn.executemany(FACT_INSERT, rows)
            total += len(rows)
        cik = int(doc.get("cik", 0))
        conn.execute(
            "INSERT INTO companies (cik,name,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(cik) DO UPDATE SET name=excluded.name",
            (cik, doc.get("entityName"),
             datetime.utcnow().isoformat(timespec="seconds")),
        )
        _log(conn, cik, "ok" if rows else "empty", len(rows))
    conn.commit()
    return total
