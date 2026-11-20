# FinSight

An agent that answers variance questions about 50 US semiconductor companies ("why did NVIDIA's gross margin change in fiscal 2025?") from SEC filings, and cites a source for every claim it makes.

## How it works

```
SEC EDGAR ──> ingest.py   10 years of XBRL facts, 50 firms (~790k facts)
          ──> mdna.py     Item 7 (MD&A) from 490 of 491 10-Ks, chunked into SQLite FTS5
metrics.py ─> annual + quarterly tables (13 metrics; Q4 = full year minus Q1-Q3)

agent.py (LangGraph)
  agent ──tool calls──> tools: sql (read-only SELECT)  |  search_filings (MD&A full-text)
    │                    each result gets a source id [S1], [S2], ...
    └──answer──> verify: cite.check() ──fails──> back to agent with the problems (max 2 rounds)
```

**The citation check** (`finsight/cite.py`) treats every sentence as a claim. A claim fails if it has no citation, cites a source that doesn't exist, states a number that isn't in its cited sources, quotes text that isn't in them word for word, or (for explanation sentences without numbers) uses key words that mostly don't appear in the passage it cites. The prompt tells the model to do all arithmetic in SQL, so derived numbers like margins and growth rates appear in a source and can be checked.

The LLM only plans tool calls and writes prose. Data, queries, checking, grading and forecasting are plain code. The model is one setting (`FINSIGHT_MODEL`, default `groq:openai/gpt-oss-120b`).

## Results

### Question answering (30-question dev set)

150 questions are generated from templates over the annual table (`eval/make_questions.py`): lookups, margins, margin changes, growth rates, R&D intensity and three-way comparisons, each with an answer computed in SQL. They are shuffled, so the first 30 cover all six types. A numeric answer is correct within 1% (or 0.15 points for percentages). Same model for all three systems.

| System | Accuracy | Claims that fail the citation check | Answers with any |
|---|---|---|---|
| **Agent** (tools + citation check) | **96.7%** (29/30) | **0** | **0/30** |
| Single prompt, data pasted in, no tools | 100% (30/30) | 64 | 27/30 |
| Single prompt, no data | 3.3% (1/30) | 67 | 30/30 |

What this shows:
- Without data, the model can't recall exact figures for these companies.
- With the company's table pasted in, one prompt gets the numbers right on these questions, but it does the arithmetic in its head, so most of its numbers can't be traced to a source. Some intermediate figures it wrote were off by 10x even when the final answer was right.
- The agent matches that accuracy and every claim checks out. Its one miss reported a 36.9% decline as +36.9.
- The agent averaged 1.4 tool calls and 13 seconds per question. The verify step sent 21 answers back for fixes over the 30 questions.

The full 150 have not been run yet: Groq's free tier allows about 200k tokens a day, so it takes 2-3 days. `python -m eval.run --all` resumes where it stopped.

### Revenue forecasting (walk-forward)

Next-quarter total revenue, all 50 firms. Each of the last 8 quarters (2024Q4 to 2026Q3) is forecast by a model trained only on earlier quarters: 365 forecasts.

| Model | MAPE | Median APE |
|---|---|---|
| **Gradient boosting** (log yoy growth from lagged growth, momentum, seasonality, sector growth, inventory) | **7.7%** | **5.5%** |
| Last year's quarter × last quarter's yoy growth | 8.9% | |
| Seasonal-naive (same quarter last year) | 18.3% | 14.9% |

Gradient boosting is 58% lower than seasonal-naive and 14% lower than the growth-adjusted baseline. Seasonal-naive is weak in this period because chip revenue grew fast; the second baseline is the fairer comparison.

Each forecast is explained in plain words by resetting one feature at a time to its typical value:

> NVDA revenue for 2026Q4 is forecast at $109.71B, +92.5% versus the same quarter last year.
> Year-over-year growth last quarter was +105.9% (typical +10.5%), which raised the forecast by about 56.9 points.

## Run it

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "GROQ_API_KEY=..." > .env                 # free key from console.groq.com
python -m finsight.ingest                      # XBRL facts, ~35 s
python -m finsight.mdna                        # MD&A text, ~15 min
python -m finsight.metrics                     # annual + quarterly tables
python -m finsight.agent "Why did AMD's operating margin change in fiscal 2024?"
python -m finsight.forecast NVDA
streamlit run app.py                           # dashboard
python -m eval.run --all                       # eval, resumable
pytest -q                                      # 11 tests, no API key needed
```

Docker: `docker build -t finsight . && docker run -p 8501:8501 -v "$PWD/data:/app/data" --env-file .env finsight` (builds the data on first run).

SEC asks automated clients for a contact in the User-Agent: set `FINSIGHT_UA="Your Name you@domain"`.

## Tests

`pytest` covers everything that doesn't need the LLM, and runs in GitHub Actions on every push: the citation check (numbers, quotes, explanations, unicode quirks), that SQL is read-only, MD&A extraction skipping the table of contents and inline references, Q4 derivation, and that forecast features only use past data.

## Limits

- Segment revenue (data center, gaming, ...) is not in SEC's company-facts API, so forecasting is on total company revenue. Segment data needs the filings' XBRL instance documents.
- The questions are templated, not written by analysts, and none of them is a "why" question, so explanation quality is checked by the citation rules but not graded for accuracy.
- The explanation check is word overlap, not meaning: it catches made-up reasons, not a subtle misreading of a passage that uses the same words.
- Qorvo is not in SEC's ticker list, so the 50 firms are the list in `finsight/ingest.py`.
- SEC's `fy` field is the fiscal year of the filing, not the period; `annual.fiscal_year` is the calendar year the fiscal period ends in.
- The Dockerfile has not been built and tested yet.
