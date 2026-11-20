"""FinSight dashboard: ask the agent, or read a revenue forecast and what drives it.

streamlit run app.py
"""
import re

import pandas as pd
import streamlit as st

from finsight import forecast

st.set_page_config(page_title="FinSight", page_icon="📑", layout="wide")
st.markdown("""<style>
h1, h2, h3 { font-family: Georgia, serif; color: #1F3B63; letter-spacing: -0.01em; }
.note { border-left: 4px solid #1F3B63; padding: 0.6rem 1rem; background: #F0EDE4; font-family: Georgia, serif; }
.src { font-family: ui-monospace, monospace; font-size: 0.8rem; white-space: pre-wrap; }
</style>""", unsafe_allow_html=True)

st.title("FinSight")
st.caption("Equity research notes on 50 US semiconductor companies, built only from SEC filings.")


@st.cache_data(show_spinner="Running the walk-forward forecast...")
def forecasts():
    df = forecast.features(forecast.load())
    return df, forecast.walk_forward(df)


ask_tab, fc_tab = st.tabs(["Ask a question", "Revenue forecast"])

with ask_tab:
    q = st.text_input("Question", "Why did NVIDIA's gross margin change in fiscal 2025?")
    if st.button("Ask", type="primary") and q:
        from finsight import agent  # needs GROQ_API_KEY; the forecast tab works without it
        with st.spinner("Querying filings..."):
            r = agent.ask(q)
        body = re.sub(r"\n?ANSWER:.*$", "", r["answer"], flags=re.S).strip()
        st.markdown(f"<div class='note'>{body}</div>", unsafe_allow_html=True)
        if r["problems"]:
            st.warning("Citation check found problems:\n\n" + "\n".join(f"- {p}" for p in r["problems"]))
        else:
            st.success(f"Every number is cited and matches its source ({len(r['sources'])} sources, "
                       f"{r['tool_calls']} tool calls).")
        for sid, text in r["sources"].items():
            with st.expander(f"[S{sid}]"):
                st.markdown(f"<div class='src'>{text[:4000]}</div>", unsafe_allow_html=True)

with fc_tab:
    df, res = forecasts()
    a, b, c = st.columns(3)
    a.metric("MAPE, gradient boosting", f"{res.ape_gbm.mean():.1%}")
    b.metric("MAPE, seasonal-naive", f"{res.ape_naive.mean():.1%}",
             f"{1 - res.ape_gbm.mean() / res.ape_naive.mean():.0%} lower with boosting", delta_color="off")
    c.metric("MAPE, last-growth baseline", f"{res.ape_drift.mean():.1%}",
             f"{1 - res.ape_gbm.mean() / res.ape_drift.mean():.0%} lower with boosting", delta_color="off")
    st.caption(f"Walk-forward: each of {res.quarter.nunique()} quarters ({res.quarter.min()} to {res.quarter.max()}) "
               "is forecast by a model trained only on earlier quarters. Total company revenue; SEC's company "
               "facts carry no segment breakdown.")

    ticker = st.selectbox("Company", sorted(df.ticker.unique()), index=sorted(df.ticker.unique()).index("NVDA"))
    hist = df[(df.ticker == ticker) & df.revenue.notna()].set_index("quarter")["revenue"]
    fc = res[res.ticker == ticker].set_index("quarter")[["gbm", "base"]]
    chart = pd.DataFrame({"Reported": hist, "Forecast (boosting)": fc.gbm, "Seasonal-naive": fc.base}) / 1e9
    chart.index = chart.index.astype(str)
    st.line_chart(chart.tail(24), y_label="Revenue, $B")
    st.subheader("Next quarter")
    first, *drivers = forecast.explain(df, ticker).split("\n")
    st.markdown(f"<div class='note'>{first}<br><br>" + "<br>".join(d.lstrip('- ') for d in drivers) + "</div>",
                unsafe_allow_html=True)
