"""Run the eval. Resumable: answers are cached in eval/results/<system>__<model>.jsonl.

python -m eval.run --system agent --limit 30
python -m eval.run --system baseline --limit 30
python -m eval.run --report
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

from finsight import agent, cite

HERE = Path(__file__).parent
RESULTS = HERE / "results"


def model_tag():
    return re.sub(r"[^\w.-]", "_", os.environ.get("FINSIGHT_MODEL", "groq:openai/gpt-oss-120b"))


def parse_answer(text):
    m = re.search(r"ANSWER:\s*\$?(-?[\d,]*\.?\d+)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def correct(q, x):
    """Percent answers: within 0.15 points or 1%. Dollar answers: within 1%, at any of the usual scales."""
    if x is None:
        return False
    g = q["gold"]
    if q["unit"] == "usd":
        return any(abs(x * k - g) <= 0.01 * abs(g) for k in (1, 1e3, 1e6, 1e9))
    return abs(x - g) <= max(0.15, 0.01 * abs(g))


def run(system, limit):
    qs = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines()][:limit]
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{system}__{model_tag()}.jsonl"
    done = {json.loads(l)["id"] for l in path.read_text().splitlines()} if path.exists() else set()
    fn = {"agent": agent.ask, "baseline": agent.baseline, "stuffed": agent.stuffed}[system]
    for q in qs:
        if q["id"] in done:
            continue
        t0 = time.time()
        r = None
        for _ in range(10):
            try:
                r = fn(q["question"])
                break
            except Exception as e:
                msg = str(e)
                if "per minute" in msg or "(TPM)" in msg or "(RPM)" in msg:
                    time.sleep(30)  # per-minute limit: wait it out
                    continue
                print(f"stopped at q{q['id']}: {type(e).__name__}: {msg[:200]}")  # e.g. daily limit: rerun tomorrow
                return
        if r is None:
            print(f"stopped at q{q['id']}: still rate limited")
            return
        x = parse_answer(r["answer"])
        # Uncited claims are counted the same way for both systems; the baseline has no sources at all.
        problems = cite.check(r["answer"], r.get("sources", {}))
        row = {"id": q["id"], "kind": q["kind"], "gold": q["gold"], "parsed": x, "correct": correct(q, x),
               "uncited": len(problems), "problems": problems, "answer": r["answer"],
               "sources": r.get("sources", {}), "tool_calls": r.get("tool_calls", 0), "fixes": r.get("fixes", 0), "seconds": round(time.time() - t0, 1)}
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"q{q['id']:>3} {q['kind']:<13} {'OK ' if row['correct'] else 'BAD'} gold={q['gold']} got={x} uncited={row['uncited']}")


def report():
    for path in sorted(RESULTS.glob("*.jsonl")):
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        for r in rows:  # rescore with the current citation check
            r["uncited"] = len(cite.check(r["answer"], {int(k): v for k, v in r.get("sources", {}).items()}))
        n = len(rows)
        acc = sum(r["correct"] for r in rows) / n
        unc = sum(r["uncited"] for r in rows)
        print(f"{path.stem:<55} n={n:<4} accuracy={acc:6.1%}  uncited claims={unc:<4} "
              f"answers with any uncited={sum(r['uncited'] > 0 for r in rows)}")
        kinds = {}
        for r in rows:
            kinds.setdefault(r["kind"], []).append(r["correct"])
        print("    " + "  ".join(f"{k}={sum(v)}/{len(v)}" for k, v in sorted(kinds.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["agent", "baseline", "stuffed"])
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    report() if a.report else run(a.system, a.limit)
