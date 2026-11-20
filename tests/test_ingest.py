from finsight.ingest import connect, rows

FACTS = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"start": "2024-01-29", "end": "2025-01-26", "val": 130497000000, "fy": 2025, "fp": "FY",
     "form": "10-K", "filed": "2025-02-26", "accn": "a1"},
    {"start": "2024-01-29", "end": "2025-01-26", "val": 1, "fy": 2025, "fp": "FY",
     "form": "8-K", "filed": "2025-02-26", "accn": "a2"},
    {"start": "2013-01-01", "end": "2013-12-31", "val": 2, "fy": 2013, "fp": "FY",
     "form": "10-K", "filed": "2014-02-01", "accn": "a3"},
]}}}}}


def test_rows_keeps_recent_10k_10q_only(tmp_path):
    out = list(rows(1045810, FACTS, min_fy=2016))
    assert [r[5] for r in out] == [130497000000]

    db = connect(tmp_path / "t.db")
    db.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", out)
    assert db.execute("SELECT val FROM facts WHERE concept='Revenues'").fetchone()[0] == 130497000000
