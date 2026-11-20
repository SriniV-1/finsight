"""Next-quarter revenue forecasts: gradient boosting vs a seasonal-naive baseline, walk-forward.

python -m finsight.forecast            # walk-forward MAPE over the last 8 quarters
python -m finsight.forecast NVDA       # also explain the latest forecast for one company

Segment revenue is not in SEC's company-facts API, so this forecasts total company revenue.
"""
import sqlite3
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from finsight.ingest import DB

TEST_QUARTERS = 8
FEATURES = {
    "yoy_1": "year-over-year growth last quarter",
    "yoy_2": "year-over-year growth two quarters ago",
    "qoq_1": "quarter-over-quarter growth last quarter",
    "seasonal": "last year's jump from the prior quarter into this one",
    "sector_yoy_1": "median year-over-year growth across all 50 firms last quarter",
    "inventory_yoy_1": "year-over-year inventory growth last quarter",
}


def load(db_path=DB):
    db = sqlite3.connect(db_path)
    q = pd.read_sql("SELECT ticker, period_end, metric, value FROM quarterly WHERE metric IN ('revenue','inventory')", db)
    q["quarter"] = pd.PeriodIndex(pd.to_datetime(q.period_end), freq="Q")
    wide = q.pivot_table(index=["ticker", "quarter"], columns="metric", values="value", aggfunc="last")
    return wide.reset_index()


def features(wide):
    """One row per (ticker, quarter) with features known before that quarter and target = log yoy growth."""
    rows = []
    for t, g in wide.groupby("ticker"):
        g = g.set_index("quarter").sort_index()
        # one extra quarter at the end: the next, not-yet-reported quarter to forecast
        g = g.reindex(pd.period_range(g.index.min(), g.index.max() + 1, freq="Q"))
        r = np.log(g["revenue"].where(g["revenue"] > 0))
        inv = np.log(g["inventory"].where(g["inventory"] > 0)) if "inventory" in g else r * np.nan
        f = pd.DataFrame({
            "ticker": t,
            "revenue": g["revenue"],
            "base": g["revenue"].shift(4),                 # seasonal-naive forecast
            "target": r - r.shift(4),                       # log yoy growth of this quarter
            "yoy_1": (r - r.shift(4)).shift(1),
            "yoy_2": (r - r.shift(4)).shift(2),
            "qoq_1": (r - r.shift(1)).shift(1),
            "seasonal": r.shift(4) - r.shift(5),
            "inventory_yoy_1": (inv - inv.shift(4)).shift(1),
        }, index=g.index)
        rows.append(f)
    df = pd.concat(rows).rename_axis("quarter").reset_index()
    sector = df.groupby("quarter")["target"].median().shift(1)
    df["sector_yoy_1"] = df.quarter.map(sector)
    df["inventory_yoy_1"] = df["inventory_yoy_1"].fillna(0)  # a few firms carry no inventory
    return df.dropna(subset=["base", "yoy_1", "yoy_2", "qoq_1", "seasonal", "sector_yoy_1"])


def model():
    return GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8,
                                     loss="huber", random_state=0)


def walk_forward(df, n_test=TEST_QUARTERS):
    """For each of the last n quarters: train on all earlier quarters, forecast that one."""
    quarters = sorted(df.quarter.unique())
    test_qs = [q for q in quarters if df[df.quarter == q].target.notna().sum() >= 25][-n_test:]
    out = []
    for q in test_qs:
        train = df[(df.quarter < q) & df.target.notna()]
        test = df[(df.quarter == q) & df.target.notna()]
        m = model().fit(train[list(FEATURES)], train.target)
        pred = test.base * np.exp(m.predict(test[list(FEATURES)]))
        out.append(test.assign(gbm=pred))
    res = pd.concat(out)
    ape = lambda p: (p - res.revenue).abs() / res.revenue
    res["drift"] = res.base * np.exp(res.yoy_1)  # tougher baseline: last year's quarter x last quarter's growth
    res["ape_gbm"], res["ape_naive"], res["ape_drift"] = ape(res.gbm), ape(res.base), ape(res.drift)
    return res


def explain(df, ticker):
    """Forecast the latest quarter for one company and say what moved it, in plain words."""
    train = df[df.target.notna()]
    m = model().fit(train[list(FEATURES)], train.target)
    row = df[(df.ticker == ticker) & df.target.isna()].sort_values("quarter").iloc[[-1]]  # next quarter
    pred = float(m.predict(row[list(FEATURES)])[0])
    lines = [f"{ticker} revenue for {row.quarter.iloc[0]} is forecast at ${float(row.base.iloc[0] * np.exp(pred)) / 1e9:.2f}B, "
             f"{100 * (np.exp(pred) - 1):+.1f}% versus the same quarter last year."]
    med = train[list(FEATURES)].median()
    effects = []
    for f, label in FEATURES.items():
        alt = row[list(FEATURES)].copy()
        alt[f] = med[f]
        effects.append((pred - float(m.predict(alt)[0]), f, label))
    for d, f, label in sorted(effects, key=lambda e: -abs(e[0]))[:3]:
        val = 100 * (np.exp(float(row[f].iloc[0])) - 1)
        typical = 100 * (np.exp(med[f]) - 1)
        lines.append(f"- {label.capitalize()} was {val:+.1f}% (typical {typical:+.1f}%), which "
                     f"{'raised' if d > 0 else 'lowered'} the forecast by about {abs(100 * d):.1f} points.")
    return "\n".join(lines)


def main(args):
    df = features(load())
    res = walk_forward(df)
    gbm, naive = res.ape_gbm.mean(), res.ape_naive.mean()
    print(f"walk-forward over {res.quarter.nunique()} quarters ({res.quarter.min()}..{res.quarter.max()}), "
          f"{len(res)} forecasts, {res.ticker.nunique()} firms")
    print(f"MAPE gradient boosting {gbm:.1%} | seasonal-naive {naive:.1%} | improvement {1 - gbm / naive:.1%}")
    print(f"median APE gradient boosting {res.ape_gbm.median():.1%} | seasonal-naive {res.ape_naive.median():.1%}")
    print(f"tougher baseline (last year's quarter x last quarter's yoy growth): MAPE {res.ape_drift.mean():.1%}, "
          f"gradient boosting improvement {1 - gbm / res.ape_drift.mean():.1%}")
    for t in args:
        print("\n" + explain(df, t.upper()))
    return res


if __name__ == "__main__":
    main(sys.argv[1:])
