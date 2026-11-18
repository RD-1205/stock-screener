"""
`ingest` company-selection tests -- no network calls (ingest.ingest_company
is monkeypatched out). Run: python tests/test_cli_ingest.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SEC_USER_AGENT", "Test Suite test@example.com")

from screener import cli, db, ingest                         # noqa: E402


def make_db():
    conn = db.connect(":memory:")
    db.init(conn)
    conn.executemany(
        "INSERT INTO companies (cik, ticker, name) VALUES (?,?,?)",
        [(1, "OK1", "Already Ingested Corp"),
         (2, "EMPTY1", "Closed-End Fund Inc"),
         (3, "NEW1", "Never Tried Corp"),
         (4, "NEW2", "Also Never Tried Corp")],
    )
    conn.executemany(
        "INSERT INTO ingest_log (cik, status, fact_count) VALUES (?,?,?)",
        [(1, "ok", 500), (2, "empty", 0)],
    )
    conn.commit()
    return conn


def _run_cmd_ingest(conn, **overrides):
    args = argparse.Namespace(zip=None, dir=None, tickers=None, refresh=False, limit=None, db=None)
    for k, v in overrides.items():
        setattr(args, k, v)
    called = []
    original = ingest.ingest_company
    ingest.ingest_company = lambda c, cik, ua: called.append(cik) or 0
    original_connect = db.connect
    db.connect = lambda path=None: conn
    try:
        cli.cmd_ingest(args)
    finally:
        ingest.ingest_company = original
        db.connect = original_connect
    return called


def test_already_confirmed_empty_is_not_retried():
    conn = make_db()
    called = _run_cmd_ingest(conn)
    assert 2 not in called, (
        "cik 2 is status='empty' -- retrying it forever was the actual bug found "
        "running this for real (45 companies re-selected on every run, 0 progress)")
    print(f"  OK  a confirmed-empty company is skipped by default: called={sorted(called)}")


def test_already_ok_is_not_retried():
    conn = make_db()
    called = _run_cmd_ingest(conn)
    assert 1 not in called
    print("  OK  a company already ingested 'ok' is not re-fetched")


def test_never_tried_companies_are_selected():
    conn = make_db()
    called = _run_cmd_ingest(conn)
    assert set(called) == {3, 4}, called
    print(f"  OK  only the genuinely new companies are selected: {sorted(called)}")


def test_refresh_flag_retries_everything_including_empty():
    """--refresh is the explicit, opt-in way to retry an empty/ok company --
    e.g. after a company starts filing XBRL it didn't file before."""
    conn = make_db()
    called = _run_cmd_ingest(conn, refresh=True)
    assert set(called) == {1, 2, 3, 4}, called
    print("  OK  --refresh retries everything, empty and ok included")


def test_explicit_tickers_bypass_the_empty_exclusion():
    """Naming a ticker directly is itself the opt-in to retry it, same as
    --refresh -- otherwise `ingest --tickers EMPTY1` would silently no-op."""
    conn = make_db()
    called = _run_cmd_ingest(conn, tickers="EMPTY1")
    assert called == [2], called
    print("  OK  naming an empty-status ticker explicitly still fetches it")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        print(f"{t.__name__}:")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {e}")
        except Exception as e:                              # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
