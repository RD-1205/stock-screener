"""
SEC EDGAR client + companyfacts parser.

Everything here is free and keyless. The only hard requirement the SEC has is
a User-Agent header containing a real contact address -- they will 403 you
without it, and they will ban the IP if you ignore the rate limit.

Rate limit: 10 requests/second. Not negotiable, not published as a header,
just enforced. We stay under it with a simple sleep.

Endpoints used:
  https://www.sec.gov/files/company_tickers.json        ticker -> CIK map
  https://data.sec.gov/submissions/CIK##########.json   filing history + metadata
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   every fact, ever

For a full-market load, do NOT hit companyfacts 10,000 times. The SEC
publishes the whole thing as a nightly bulk zip (~1.5 GB decompressed):
  https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
One download, then parse locally. `parse_companyfacts` works on either.
"""

import json
import time
import urllib.request
import urllib.error

from .concepts import INTERESTING_CONCEPTS

BASE_SEC = "https://www.sec.gov"
BASE_DATA = "https://data.sec.gov"
BULK_COMPANYFACTS = f"{BASE_SEC}/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# SEC allows 10 req/s. 0.12s gives headroom for clock jitter.
MIN_INTERVAL = 0.12
_last_request = [0.0]


class EdgarError(Exception):
    pass


def _throttle():
    elapsed = time.monotonic() - _last_request[0]
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request[0] = time.monotonic()


def get_json(url, user_agent, retries=3):
    """GET with the SEC's required User-Agent, throttling, and backoff."""
    if not user_agent or "@" not in user_agent:
        raise EdgarError(
            "SEC requires a User-Agent with a contact email, "
            "e.g. 'Rudra Arora rudra@example.com'. Set SEC_USER_AGENT."
        )
    last_err = None
    for attempt in range(retries):
        _throttle()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                      # company has no XBRL facts
            if e.code in (429, 503):             # throttled -- back off hard
                time.sleep(2 ** attempt)
                last_err = e
                continue
            raise EdgarError(f"{e.code} for {url}") from e
        except Exception as e:                   # noqa: BLE001 - network flake
            last_err = e
            time.sleep(2 ** attempt)
    raise EdgarError(f"failed after {retries} tries: {url} ({last_err})")


def cik_str(cik):
    """EDGAR wants CIK zero-padded to 10 digits in URLs."""
    return f"CIK{int(cik):010d}"


def fetch_tickers(user_agent):
    """Returns [{cik, ticker, name}] for every listed filer (~10k rows)."""
    data = get_json(f"{BASE_SEC}/files/company_tickers.json", user_agent)
    out = []
    for row in (data or {}).values():
        out.append({
            "cik": int(row["cik_str"]),
            "ticker": row["ticker"].strip().upper(),
            "name": row["title"].strip(),
        })
    return out


def fetch_submissions(cik, user_agent):
    return get_json(f"{BASE_DATA}/submissions/{cik_str(cik)}.json", user_agent)


def fetch_companyfacts(cik, user_agent):
    return get_json(f"{BASE_DATA}/api/xbrl/companyfacts/{cik_str(cik)}.json", user_agent)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_companyfacts(doc, only_interesting=True):
    """Flatten the deeply-nested companyfacts JSON into fact rows.

    Input shape:
        {"cik":320193, "entityName":"Apple Inc.",
         "facts": {"us-gaap": {"NetIncomeLoss": {
             "label": "...",
             "units": {"USD": [
                 {"start":"2023-01-01","end":"2023-04-01","val":24160000000,
                  "accn":"0000320193-23-000064","fy":2023,"fp":"Q2",
                  "form":"10-Q","filed":"2023-05-05","frame":"CY2023Q1"},
                 ...
             ]}}}}}

    Note `start` is absent on instant facts (balance sheet items). We store ''
    rather than NULL so the primary key stays portable to Postgres.
    """
    if not doc:
        return []
    cik = int(doc.get("cik", 0))
    rows = []
    for taxonomy, concepts in (doc.get("facts") or {}).items():
        for concept, body in concepts.items():
            if only_interesting and concept not in INTERESTING_CONCEPTS:
                continue
            for unit, entries in (body.get("units") or {}).items():
                for e in entries:
                    if e.get("val") is None:
                        continue
                    rows.append((
                        cik,
                        taxonomy,
                        concept,
                        unit,
                        e.get("start") or "",
                        e.get("end"),
                        e.get("fy"),
                        e.get("fp"),
                        e.get("form"),
                        e.get("filed"),
                        e.get("accn") or "",
                        e.get("frame"),
                        float(e["val"]),
                    ))
    return rows


def company_meta_from_submissions(doc):
    if not doc:
        return None
    exchanges = (doc.get("exchanges") or [])
    tickers = (doc.get("tickers") or [])
    return {
        "cik": int(doc["cik"]),
        "ticker": tickers[0].upper() if tickers else None,
        "name": doc.get("name"),
        "sic": doc.get("sic"),
        "sic_description": doc.get("sicDescription"),
        "exchange": exchanges[0] if exchanges else None,
        "fiscal_year_end": doc.get("fiscalYearEnd"),
    }
