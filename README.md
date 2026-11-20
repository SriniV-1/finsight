# FinSight

An agent that answers variance questions about semiconductor companies ("why did data-center gross margin move?") from SEC filings, citing every figure it uses.

## Status

| Piece | State |
|---|---|
| XBRL financials, 50 firms, 10 fiscal years, into SQLite | Done (`python -m finsight.ingest`, ~790k facts, ~35 s) |
| MD&A text from 10-Ks, chunked for retrieval | Next |
| LangGraph agent: SQL tool + retrieval tool + citation check | To do |
| Eval set of 150 analyst-written questions | To do |
| Segment-revenue forecasting vs seasonal-naive baseline | To do |
| Dashboard explaining each forecast's drivers | To do |
| Docker | To do |

## Minimum targets

These are the bar to clear. None has been measured yet.

- At least 90% numeric accuracy on the 150-question eval, against a single-prompt baseline
- 0 uncited claims
- Forecasting beats seasonal-naive MAPE by at least 15% on a walk-forward holdout
- Regression tests run in GitHub Actions on every push

## Setup

```
python -m finsight.ingest            # all 50 firms
python -m finsight.ingest NVDA AMD   # a few
pytest -q
```

Standard library only so far. SEC requires a contact in the User-Agent: set `FINSIGHT_UA="Your Name you@domain"`.

## Data notes

- Source: `data.sec.gov/api/xbrl/companyfacts`, us-gaap facts from 10-K and 10-Q filings only.
- SEC's `fy` is the fiscal year of the *filing*, not the period. A 10-K for FY2026 also reports FY2025 and FY2024. Use `start`/`end` to pick a period.
- Companies tag revenue under different concepts (`Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, ...).
- Company facts have no segment (dimensional) data. Segment revenue needs the filings' XBRL instance documents.
- Qorvo is not in SEC's ticker list, so the 50 firms are the list in `finsight/ingest.py`.
