"""Normalize raw XBRL facts into tidy `annual` and `quarterly` tables.

python -m finsight.metrics
"""
import pandas as pd

from finsight.ingest import connect

# metric -> us-gaap concepts in priority order (companies tag the same thing differently)
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "rd_expense": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "inventory": ["InventoryNet"],
    "total_assets": ["Assets"],
}
INSTANT = {"cash", "inventory", "total_assets"}  # balance-sheet values have no start date
FLOW_FOR_Q4 = {"revenue", "cost_of_revenue", "gross_profit", "rd_expense", "sga_expense",
               "operating_income", "net_income", "operating_cash_flow", "capex"}

SCHEMA = """
DROP TABLE IF EXISTS annual; DROP TABLE IF EXISTS quarterly;
CREATE TABLE annual (ticker TEXT, fiscal_year INTEGER, period_end TEXT, metric TEXT, value REAL, accn TEXT,
                     PRIMARY KEY (ticker, fiscal_year, metric));
CREATE TABLE quarterly (ticker TEXT, period_end TEXT, metric TEXT, value REAL, accn TEXT, derived INTEGER,
                        PRIMARY KEY (ticker, period_end, metric));
"""


def load(db):
    names = {c: (m, i) for m, cs in CONCEPTS.items() for i, c in enumerate(cs)}
    q = f"""SELECT c.ticker, f.concept, f.unit, f.start, f.end, f.val, f.form, f.filed, f.accn
            FROM facts f JOIN companies c USING (cik)
            WHERE f.concept IN ({','.join('?' * len(names))})"""
    df = pd.read_sql(q, db, params=list(names))
    df["metric"] = df.concept.map(lambda c: names[c][0])
    df["priority"] = df.concept.map(lambda c: names[c][1])
    df = df[(df.unit == "USD") | (df.metric == "eps_diluted")]
    df["days"] = (pd.to_datetime(df.end) - pd.to_datetime(df.start)).dt.days
    return df


def pick(df):
    """One value per (ticker, metric, period): best concept first, then the most recent filing (restatements win)."""
    df = df.sort_values(["priority", "filed"], ascending=[True, False])
    return df.drop_duplicates(["ticker", "metric", "start", "end"]).drop_duplicates(["ticker", "metric", "end"])


def build(db):
    df = load(db)
    instant = df[df.metric.isin(INSTANT) & df.start.isna()]
    flows = df[~df.metric.isin(INSTANT) & df.start.notna()]

    # Annual: one-year flows from 10-Ks, plus year-end balances from 10-Ks.
    fy = pick(flows[(flows.form == "10-K") & flows.days.between(350, 380)])
    fy_bal = pick(instant[instant.form == "10-K"])
    fy_bal = fy_bal[fy_bal.set_index(["ticker", "end"]).index.isin(fy.set_index(["ticker", "end"]).index)]
    annual = pd.concat([fy, fy_bal])
    annual["fiscal_year"] = annual.end.str[:4].astype(int)
    annual = annual.sort_values("filed").drop_duplicates(["ticker", "fiscal_year", "metric"], keep="last")

    # Quarterly: three-month flows, plus Q4 = full year minus the three quarters inside it.
    q = pick(flows[flows.days.between(80, 100)])[["ticker", "metric", "start", "end", "val", "accn"]].assign(derived=0)
    q4 = []
    for (t, m), year in fy[fy.metric.isin(FLOW_FOR_Q4)].groupby(["ticker", "metric"]):
        qs = q[(q.ticker == t) & (q.metric == m)]
        for _, row in year.iterrows():
            inside = qs[(qs.start >= row.start) & (qs.end < row.end)]
            if len(inside) == 3 and not (qs.end == row.end).any():
                q4.append({"ticker": t, "metric": m, "start": inside.end.max(), "end": row.end,
                           "val": row.val - inside.val.sum(), "accn": row.accn, "derived": 1})
    quarterly = pd.concat([q, pd.DataFrame(q4)])

    db.executescript(SCHEMA)
    db.executemany("INSERT INTO annual VALUES (?,?,?,?,?,?)",
                   annual[["ticker", "fiscal_year", "end", "metric", "val", "accn"]].itertuples(index=False))
    db.executemany("INSERT INTO quarterly VALUES (?,?,?,?,?,?)",
                   quarterly[["ticker", "end", "metric", "val", "accn", "derived"]].itertuples(index=False))
    # Some firms never tag GrossProfit; derive it where both parts exist.
    for table, key in (("annual", "fiscal_year"), ("quarterly", "period_end")):
        extra = "r.derived" if table == "quarterly" else ""
        db.execute(f"""INSERT OR IGNORE INTO {table}
            SELECT r.ticker, r.{key}, {'' if table == 'quarterly' else 'r.period_end, '}'gross_profit',
                   r.value - c.value, r.accn{', ' + extra if extra else ''}
            FROM {table} r JOIN {table} c ON c.ticker = r.ticker AND c.{key} = r.{key}
            WHERE r.metric = 'revenue' AND c.metric = 'cost_of_revenue'""")
    db.commit()
    return len(annual), len(quarterly)


if __name__ == "__main__":
    print("annual %d rows, quarterly %d rows" % build(connect()))
