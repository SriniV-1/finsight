"""The agent's two skills: read-only SQL over the metrics, and full-text search over MD&A."""
import re
import sqlite3

from finsight.ingest import DB

MAX_ROWS = 40

SCHEMA_DOC = """Tables (SQLite):
annual(ticker, fiscal_year, period_end, metric, value, accn)
quarterly(ticker, period_end, metric, value, accn, derived)   -- derived=1: Q4 computed as full year minus Q1-Q3
companies(cik, ticker, name)
metric is one of: revenue, cost_of_revenue, gross_profit, rd_expense, sga_expense, operating_income,
net_income, eps_diluted, operating_cash_flow, capex, cash, inventory, total_assets.
value is in US dollars (eps_diluted in dollars per share). Long format: one row per metric, so pivot with
MAX(CASE WHEN metric='revenue' THEN value END).
fiscal_year is the calendar year the fiscal year ends in (NVIDIA's FY2025 ended 2025-01-26).
Quarters are identified by period_end; there is no quarter label column."""


def _ro():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.execute("PRAGMA query_only = 1")
    return db


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


def run_sql(query: str) -> str:
    """Run one read-only SELECT. Returns a pipe table or an error message the model can fix."""
    if not re.match(r"\s*(select|with)\b", query, re.I) or ";" in query.strip().rstrip(";"):
        return "ERROR: only a single SELECT (or WITH ... SELECT) statement is allowed."
    try:
        with _ro() as db:
            cur = db.execute(query)
            cols = [c[0] for c in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
    except sqlite3.Error as e:
        return f"ERROR: {e}"
    if not rows:
        return "(no rows)"
    lines = [" | ".join(cols)] + [" | ".join(_fmt(v) for v in r) for r in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines.append(f"... more than {MAX_ROWS} rows, narrow the query")
    return "\n".join(lines)


def search_filings(query: str, ticker: str = "", year: int = 0, k: int = 4) -> str:
    """Full-text search over 10-K MD&A sections. year = calendar year of the fiscal period end."""
    terms = " OR ".join(f'"{w}"' for w in re.findall(r"[A-Za-z0-9\-]{3,}", query))
    if not terms:
        return "ERROR: empty query."
    sql = """SELECT c.ticker, m.period, m.accn, m.text FROM mdna m JOIN companies c ON c.cik = m.cik
             WHERE mdna MATCH ?"""
    args = [terms]
    if ticker:
        sql += " AND c.ticker = ?"
        args.append(ticker.upper())
    if year:
        sql += " AND substr(m.period, 1, 4) = ?"
        args.append(str(year))
    sql += " ORDER BY bm25(mdna) LIMIT ?"
    args.append(k)
    with _ro() as db:
        rows = db.execute(sql, args).fetchall()
    if not rows:
        return "(no matching passages)"
    return "\n\n---\n\n".join(f"{t} 10-K, period ending {p} (accession {a}):\n{text}" for t, p, a, text in rows)
