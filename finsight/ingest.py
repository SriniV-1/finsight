"""Download XBRL company facts from SEC EDGAR into SQLite.

python -m finsight.ingest            # all 50 firms, last 10 fiscal years
python -m finsight.ingest NVDA AMD   # just these
"""
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

# SEC asks automated clients to identify themselves. Set FINSIGHT_UA to "Name contact@domain".
import os

UA = os.environ.get("FINSIGHT_UA", "FinSight research-tool finsight-research@example.com")
DB = Path(os.environ.get("FINSIGHT_DB", "data/finsight.db"))
YEARS = 10

TICKERS = (
    "NVDA AMD INTC AVGO QCOM TXN MU ADI AMAT LRCX KLAC MRVL NXPI MCHP ON MPWR SWKS TER ENTG MKSI "
    "LSCC CRUS SLAB SYNA POWI DIOD AMKR WOLF RMBS SMTC FORM ACLS COHU ONTO UCTT ICHR VECO PLAB AOSL "
    "MXL SITM AMBA ALGM KLIC MTSI PI CRDO INDI NVEC CEVA"
).split()

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (cik INTEGER PRIMARY KEY, ticker TEXT UNIQUE, name TEXT);
CREATE TABLE IF NOT EXISTS facts (
    cik INTEGER, concept TEXT, unit TEXT, start TEXT, end TEXT, val REAL,
    fy INTEGER, fp TEXT, form TEXT, filed TEXT, accn TEXT,
    PRIMARY KEY (cik, concept, unit, start, end, accn)
);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts (cik, concept, end);
"""


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def rows(cik, facts, min_fy):
    """Flatten companyfacts JSON into fact rows from 10-K/10-Q filings, fiscal year >= min_fy."""
    for concept, body in facts.get("facts", {}).get("us-gaap", {}).items():
        for unit, items in body["units"].items():
            for f in items:
                if f.get("form") not in ("10-K", "10-Q") or (f.get("fy") or 0) < min_fy:
                    continue
                yield (cik, concept, unit, f.get("start"), f["end"], f["val"],
                       f.get("fy"), f.get("fp"), f["form"], f.get("filed"), f["accn"])


def connect(path=DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def main(tickers):
    index = {v["ticker"]: v for v in get("https://www.sec.gov/files/company_tickers.json").values()}
    db = connect()
    min_fy = time.gmtime().tm_year - YEARS
    for t in tickers:
        cik = index[t]["cik_str"]
        facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        db.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?)", (cik, t, index[t]["title"]))
        n = db.executemany("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           rows(cik, facts, min_fy)).rowcount
        db.commit()
        print(f"{t:5} {n:>7} facts")
        time.sleep(0.15)  # SEC limit is 10 requests/second


if __name__ == "__main__":
    main(sys.argv[1:] or TICKERS)
