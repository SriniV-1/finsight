import sqlite3

import numpy as np
import pandas as pd

from finsight import cite, forecast, metrics, mdna, tools
from finsight.ingest import connect

SRC = {1: "fiscal_year | revenue\n2025 | 130497000000\n2024 | 60922000000", 2: "gm\n74.9887"}


def test_cite_accepts_rounded_cited_numbers():
    assert cite.check("Revenue was $130.5 billion in FY2025 [S1]. Gross margin was 75.0% [S2].", SRC) == []


def test_cite_flags_uncited_wrong_and_missing_sources():
    assert cite.check("Revenue was $130.5 billion.", SRC)
    assert cite.check("Revenue was $99 billion [S1].", SRC)
    assert cite.check("Revenue was $130.5 billion [S9].", SRC)


def test_cite_ignores_years_and_labels():
    assert cite.check("Revenue for fiscal 2025 and Q3 FY2024 is listed [S1].", SRC) == []


MDNA = {3: "Gross margin increased, primarily driven by a higher mix of Data Center revenue and Hopper demand."}


def test_cite_checks_quotes_and_explanations():
    assert cite.check("Management said it was “primarily driven by a higher mix of Data Center revenue” [S3].", MDNA) == []
    assert cite.check("Management said it was “driven by lower inventory charges” [S3].", MDNA)
    assert cite.check("The rise reflected a higher Data Center mix with Hopper demand [S3].", MDNA) == []
    assert cite.check("The rise came from cheaper wafer pricing negotiated with foundries [S3].", MDNA)
    assert cite.check("Margins improved because demand was strong.", MDNA)  # no citation at all


def test_sql_is_read_only(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    sqlite3.connect(db).executescript("CREATE TABLE t (x); INSERT INTO t VALUES (1);")
    monkeypatch.setattr(tools, "DB", db)
    assert tools.run_sql("SELECT x FROM t") == "x\n1"
    assert tools.run_sql("DROP TABLE t").startswith("ERROR")
    assert tools.run_sql("SELECT 1; DELETE FROM t").startswith("ERROR")
    assert tools.run_sql("WITH a AS (SELECT 1) DELETE FROM t").startswith("ERROR")
    assert tools.run_sql("SELECT count(*) AS n FROM t") == "n\n1"


def test_mdna_skips_toc_and_inline_references():
    body = "Revenue rose. " * 300
    text = ("Item 7. Management's Discussion and Analysis 35\nItem 8. Financial Statements and Supplementary Data 48\n"
            f"\nITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n{body}\nSee Part II, Item 8: Financial Statements and "
            f"Supplementary Data.\n{body}\nITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\nBalance sheet")
    out = mdna.extract_mdna(text)
    assert out.startswith("ITEM 7.") and "See Part II" in out and "Balance sheet" not in out


def test_q4_is_full_year_minus_three_quarters(tmp_path):
    db = connect(tmp_path / "t.db")
    db.execute("INSERT INTO companies VALUES (1, 'ABC', 'Abc Inc')")
    q = [("2024-01-01", "2024-03-31", 10), ("2024-04-01", "2024-06-30", 20), ("2024-07-01", "2024-09-30", 30)]
    rows = [(1, "Revenues", "USD", s, e, v, 2024, "Q", "10-Q", "2024-11-01", f"q{i}") for i, (s, e, v) in enumerate(q)]
    rows.append((1, "Revenues", "USD", "2024-01-01", "2024-12-31", 100, 2024, "FY", "10-K", "2025-02-01", "k"))
    db.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    metrics.build(db)
    assert db.execute("SELECT value, derived FROM quarterly WHERE period_end='2024-12-31'").fetchone() == (40, 1)
    assert db.execute("SELECT value FROM annual WHERE fiscal_year=2024 AND metric='revenue'").fetchone() == (100,)


def test_forecast_features_only_use_the_past():
    quarters = pd.period_range("2019Q1", periods=16, freq="Q")
    wide = pd.DataFrame({"ticker": "ABC", "quarter": quarters, "revenue": np.exp(np.arange(16) * 0.1), "inventory": 1.0})
    df = forecast.features(wide).set_index("quarter")
    t = df.index[2]
    assert np.isclose(df.loc[t, "yoy_1"], df.loc[t - 1, "target"])  # feature at t is last quarter's outcome
    assert df.target.isna().iloc[-1]                                # one extra row: the quarter to forecast


def test_cite_accepts_fullwidth_brackets_and_nbsp():
    assert cite.check("Operating margin was **4.85 %** of revenue in fiscal 2020【S1】.", {1: "m\n4.85369"}) == []
