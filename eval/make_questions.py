"""Generate the eval set: 150 templated questions, each with a gold answer computed in SQL.

python -m eval.make_questions   -> eval/questions.jsonl
"""
import json
import random
import re
import sqlite3
from pathlib import Path

from finsight.ingest import DB

YEARS = range(2018, 2026)
OUT = Path(__file__).parent / "questions.jsonl"


def table(db):
    """{(ticker, year): {metric: value}} for annual data."""
    t = {}
    for ticker, fy, metric, value in db.execute("SELECT ticker, fiscal_year, metric, value FROM annual"):
        t.setdefault((ticker, fy), {})[metric] = value
    return t


def names(db):
    clean = lambda n: re.sub(r"/[^/]*/|[,.]|\b(inc|corp|corporation|incorporated|ltd|plc|n\.?v|co|holdings?)\b", "", n, flags=re.I)
    return {t: " ".join(clean(n).split()).title() for t, n in db.execute("SELECT ticker, name FROM companies")}


def pct(a, b):
    return 100 * a / b


def main(seed=7):
    db = sqlite3.connect(DB)
    data, nm = table(db), names(db)
    rng = random.Random(seed)
    keys = [k for k in data if k[1] in YEARS]
    co = lambda t: f"{nm[t]} ({t})"
    qs = []

    def add(kind, q, gold, unit):
        qs.append({"id": len(qs) + 1, "kind": kind, "question": q, "gold": round(gold, 4), "unit": unit})

    def pick(n, need, prev=False):
        pool = [k for k in keys if all(m in data[k] for m in need)
                and (not prev or ((k[0], k[1] - 1) in data and all(m in data[(k[0], k[1] - 1)] for m in need)))]
        return rng.sample(pool, n)

    for t, y in pick(35, ["revenue", "net_income", "rd_expense"]):
        m = rng.choice(["revenue", "net_income", "rd_expense"])
        label = {"revenue": "total revenue", "net_income": "net income", "rd_expense": "research and development expense"}[m]
        add("lookup", f"What was {co(t)}'s {label} in fiscal {y}, in US dollars?", data[(t, y)][m], "usd")
    for t, y in pick(30, ["revenue", "gross_profit", "operating_income"]):
        m = rng.choice(["gross_profit", "operating_income"])
        label = "gross margin" if m == "gross_profit" else "operating margin"
        add("margin", f"What was {co(t)}'s {label} in fiscal {y}, as a percent of revenue?",
            pct(data[(t, y)][m], data[(t, y)]["revenue"]), "pct")
    for t, y in pick(25, ["revenue", "gross_profit"], prev=True):
        a, b = data[(t, y)], data[(t, y - 1)]
        add("margin_change", f"By how many percentage points did {co(t)}'s gross margin change from fiscal {y-1} "
            f"to fiscal {y}? (Negative if it fell.)", pct(a["gross_profit"], a["revenue"]) - pct(b["gross_profit"], b["revenue"]), "pp")
    for t, y in pick(25, ["revenue", "operating_income"], prev=True):
        m = rng.choice(["revenue", "operating_income"])
        a, b = data[(t, y)][m], data[(t, y - 1)][m]
        if b <= 0:
            continue
        add("growth", f"By what percent did {co(t)}'s {m.replace('_', ' ')} change from fiscal {y-1} to fiscal {y}?",
            pct(a - b, b), "pct")
    for t, y in pick(20, ["revenue", "rd_expense"]):
        add("ratio", f"What was {co(t)}'s R&D expense as a percent of revenue in fiscal {y}?",
            pct(data[(t, y)]["rd_expense"], data[(t, y)]["revenue"]), "pct")
    while len(qs) < 150:
        y = rng.choice(list(YEARS))
        group = [t for t in rng.sample(sorted(nm), 12) if {"revenue", "gross_profit"} <= set(data.get((t, y), {}))][:3]
        if len(group) < 3:
            continue
        best = max(group, key=lambda t: pct(data[(t, y)]["gross_profit"], data[(t, y)]["revenue"]))
        add("compare", f"Among {', '.join(co(t) for t in group)}, which had the highest gross margin in fiscal {y}, "
            f"and what was it (percent of revenue)?", pct(data[(best, y)]["gross_profit"], data[(best, y)]["revenue"]), "pct")

    qs = qs[:150]
    rng.shuffle(qs)  # so any prefix (a dev subset) mixes all question types
    for i, q in enumerate(qs, 1):
        q["id"] = i
    OUT.write_text("".join(json.dumps(q) + "\n" for q in qs))
    print(f"wrote {len(qs[:150])} questions to {OUT}")


if __name__ == "__main__":
    main()
