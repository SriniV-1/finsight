"""LangGraph agent: plan -> call SQL / filing search -> answer -> citation check (retry on failure).

python -m finsight.agent "Why did NVIDIA's gross margin change in fiscal 2025?"
"""
import operator
import os
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from finsight import cite, tools

MAX_FIXES = 2

for line in (Path(__file__).parent.parent / ".env").read_text().splitlines() if (Path(__file__).parent.parent / ".env").exists() else []:
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip())


def llm():
    """Model is one env var: FINSIGHT_MODEL=groq:openai/gpt-oss-120b, google_genai:gemini-2.5-flash, ..."""
    return init_chat_model(os.environ.get("FINSIGHT_MODEL", "groq:openai/gpt-oss-120b"), temperature=0, max_retries=8)


@tool
def sql(query: str) -> str:
    """Run one read-only SQLite SELECT over the financial tables. Do all arithmetic (differences, margins,
    growth rates) inside the query so every number you report appears in a result."""
    return tools.run_sql(query)


@tool
def search_filings(query: str, ticker: str = "", year: int = 0) -> str:
    """Search 10-K Management's Discussion & Analysis text for explanations (the 'why').
    year is the calendar year the fiscal period ends in. Returns the best matching passages."""
    return tools.search_filings(query, ticker, year)


TOOLS = {t.name: t for t in (sql, search_filings)}

SYSTEM = f"""You are a financial analyst answering questions about 50 US-listed semiconductor companies
using only SEC filings data reached through your tools.

{tools.SCHEMA_DOC}

Rules:
- Get every figure from a tool. Never use numbers from memory.
- Compute derived figures (changes, margins, growth, ratios) in SQL so the exact value appears in a result.
- Each tool result starts with a source id like [S3]. Every sentence that contains a number must end with
  the id(s) of the source(s) that contain that number.
- For "why" questions, combine the numbers from SQL with explanations from search_filings, citing both.
- Be brief: at most 5 sentences.
- Finish with a last line `ANSWER: <the single key number, plain digits, no units or commas> [S#]`,
  using the same scale as the question asks (percent as e.g. 75.0, dollars as e.g. 130497000000)."""


class State(TypedDict):
    messages: Annotated[list, add_messages]
    sources: dict
    fixes: int
    problems: list


def agent_node(state):
    model = llm().bind_tools(list(TOOLS.values()))
    return {"messages": [model.invoke(state["messages"])]}


def tools_node(state):
    sources = dict(state["sources"])
    out = []
    for call in state["messages"][-1].tool_calls:
        sid = len(sources) + 1
        try:
            result = TOOLS[call["name"]].invoke(call["args"])
        except Exception as e:  # bad arguments go back to the model as text
            result = f"ERROR: {e}"
        sources[sid] = result
        out.append(ToolMessage(f"[S{sid}]\n{result}", tool_call_id=call["id"]))
    return {"messages": out, "sources": sources}


def verify_node(state):
    problems = cite.check(state["messages"][-1].content, state["sources"])
    if problems and state["fixes"] < MAX_FIXES:
        note = "Your answer failed the citation check:\n- " + "\n- ".join(problems[:8]) + \
               "\nFix it: run queries for missing figures or remove unsupported numbers, then answer again."
        return {"messages": [HumanMessage(note)], "fixes": state["fixes"] + 1, "problems": problems}
    return {"problems": problems}


def route_agent(state):
    return "tools" if state["messages"][-1].tool_calls else "verify"


def route_verify(state):
    return "agent" if isinstance(state["messages"][-1], HumanMessage) else END


def build():
    g = StateGraph(State)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("verify", verify_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent, ["tools", "verify"])
    g.add_edge("tools", "agent")
    g.add_conditional_edges("verify", route_verify, ["agent", END])
    return g.compile()


GRAPH = build()


def ask(question):
    state = GRAPH.invoke(
        {"messages": [SystemMessage(SYSTEM), HumanMessage(question)], "sources": {}, "fixes": 0, "problems": []},
        {"recursion_limit": 40},
    )
    final = next(m for m in reversed(state["messages"]) if isinstance(m, AIMessage))
    return {"answer": final.content, "sources": state["sources"], "problems": state["problems"],
            "tool_calls": sum(len(m.tool_calls) for m in state["messages"] if isinstance(m, AIMessage)),
            "fixes": state["fixes"]}


def baseline(question):
    """Single prompt, same model, no tools: the comparison point."""
    msg = llm().invoke([SystemMessage("You are a financial analyst. Answer from what you know in at most 5 sentences. "
                                      "Finish with a last line `ANSWER: <the single key number, plain digits, no units "
                                      "or commas>` (percent as e.g. 75.0, dollars as e.g. 130497000000)."),
                        HumanMessage(question)])
    return {"answer": msg.content}


def stuffed(question):
    """Single prompt with the data pasted in: each mentioned company's full annual table, no tools, no retries.
    Separates what the agent loop adds from simply having the numbers."""
    import re as _re
    tickers = _re.findall(r"\(([A-Z]{1,5})\)", question)
    tables = []
    for i, t in enumerate(tickers, 1):
        cols = ", ".join(f"MAX(CASE WHEN metric='{m}' THEN value END) AS {m}" for m in
                         ("revenue", "cost_of_revenue", "gross_profit", "rd_expense", "operating_income", "net_income"))
        rows = tools.run_sql(f"SELECT fiscal_year, {cols} FROM annual WHERE ticker = '{t}' AND fiscal_year >= 2014 "
                             "GROUP BY fiscal_year ORDER BY fiscal_year")
        tables.append(f"[S{i}] {t} annual financials (US dollars):\n{rows}")
    sources = {i: t for i, t in enumerate(tables, 1)}
    msg = llm().invoke([SystemMessage(
        "You are a financial analyst. Answer using only the data below, in at most 5 sentences, citing it as [S#]. "
        "Finish with a last line `ANSWER: <the single key number, plain digits, no units or commas>` "
        "(percent as e.g. 75.0, dollars as e.g. 130497000000).\n\n" + "\n\n".join(tables)),
        HumanMessage(question)])
    return {"answer": msg.content, "sources": sources}


if __name__ == "__main__":
    r = ask(" ".join(sys.argv[1:]) or "What was NVIDIA's gross margin in fiscal 2025, and why did it change?")
    print(r["answer"], "\n")
    for sid, text in r["sources"].items():
        print(f"[S{sid}] {text[:300]}\n")
    print("citation problems:", r["problems"] or "none", "| tool calls:", r["tool_calls"], "| fixes:", r["fixes"])
