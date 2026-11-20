"""Download MD&A (10-K Item 7) text and index it for full-text search.

python -m finsight.mdna            # all firms already in the companies table
python -m finsight.mdna NVDA       # just these
"""
import html
import re
import sys
import time
from html.parser import HTMLParser

from finsight.ingest import YEARS, connect, get, UA
import urllib.request

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (accn TEXT PRIMARY KEY, cik INTEGER, period TEXT, url TEXT, has_mdna INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS mdna USING fts5(text, accn UNINDEXED, cik UNINDEXED, period UNINDEXED, chunk UNINDEXED);
"""
CHUNK = 1500  # characters


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def to_text(raw):
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text)


START = re.compile(r"^[ \t]*(item\s*7\s*[.:\-—–]?\s*)?management[’']s\s+discussion\s+and\s+analysis", re.I | re.M)
END = re.compile(r"^[ \t]*(item\s*(7a|8)\s*[.:\-—–]?\s*)?(quantitative\s+and\s+qualitative\s+disclosures"
                 r"|financial\s+statements\s+and\s+supplement)", re.I | re.M)


def extract_mdna(text):
    """Item 7 runs from its heading to Item 7A/8. The table of contents also matches, so keep the longest span."""
    best = ""
    for m in START.finditer(text):
        end = END.search(text, m.end())
        span = text[m.start(): end.start() if end else len(text)]
        if len(span) > len(best):
            best = span
    return best if len(best) > 2000 else ""


def chunks(text, size=CHUNK):
    """Paragraph-packed chunks of about `size` characters."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def tenk_list(cik):
    """(form, accession, document, period) for every filing; older ones sit on extra pages."""
    sub = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")["filings"]
    pages = [sub["recent"]] + [get(f"https://data.sec.gov/submissions/{f['name']}") for f in sub.get("files", [])]
    for p in pages:
        yield from zip(p["form"], p["accessionNumber"], p["primaryDocument"], p["reportDate"])


def main(tickers):
    db = connect()
    db.executescript(SCHEMA)
    q = "SELECT cik, ticker FROM companies" + (f" WHERE ticker IN ({','.join('?' * len(tickers))})" if tickers else "")
    min_year = time.gmtime().tm_year - YEARS
    for cik, ticker in db.execute(q, tickers).fetchall():
        got = 0
        for form, accn, doc, period in tenk_list(cik):
            if form != "10-K" or not period or int(period[:4]) < min_year:
                continue
            if db.execute("SELECT 1 FROM filings WHERE accn=?", (accn,)).fetchone():
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{doc}"
            try:
                section = extract_mdna(to_text(fetch(url)))
            except Exception as e:  # one bad filing shouldn't stop the run
                print(f"  {ticker} {period} failed: {e}")
                continue
            parts = chunks(section)
            db.executemany("INSERT INTO mdna VALUES (?,?,?,?,?)",
                           [(t, accn, cik, period, i) for i, t in enumerate(parts)])
            db.execute("INSERT INTO filings VALUES (?,?,?,?,?)", (accn, cik, period, url, int(bool(parts))))
            db.commit()
            got += bool(parts)
            time.sleep(0.15)
        print(f"{ticker:5} {got} MD&A sections")


if __name__ == "__main__":
    main(sys.argv[1:])
