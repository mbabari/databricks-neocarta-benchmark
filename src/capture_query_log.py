"""Capture the Databricks query history of one question, WITH vs WITHOUT Neocarta.

Every statement that reaches the Databricks SQL warehouse flows through
``runtime._fetch`` (the discovery tools ``list_schemas`` / ``list_tables`` /
``get_table_columns`` and the ``execute_sql`` tool all call it). We monkeypatch
that one function to record each SQL string with its wall-clock time, run the
same question in each mode, and dump both logs to ``docs/query_log.json``.

    uv run python src/capture_query_log.py --qid q6 --model gpt-4o-mini

WITHOUT: the agent brute-forces the catalog -> a long list of information_schema
scans. WITH: retrieval happens in Neo4j over MCP (never touches the warehouse),
so Databricks only sees the single final business query.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env", override=True)

os.environ.setdefault("DEMO_QUIET", "0")  # keep stderr; don't redirect

import litellm  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_litellm import ChatLiteLLM  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402

import runtime  # noqa: E402
from runtime import (  # noqa: E402
    build_tools_prompt,
    catalog,
    estimate_cost,
    load_questions,
    make_mcp_client,
    tally,
)

litellm.suppress_debug_info = True

_CAPTURE: list[dict] = []
_ORIG_FETCH = runtime._fetch


def _capturing_fetch(sql_text: str, limit=None):
    start = time.time()
    error = None
    try:
        return _ORIG_FETCH(sql_text, limit=limit)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:120]
        raise
    finally:
        _CAPTURE.append(
            {
                "sql": " ".join(sql_text.split()),
                "ms": round((time.time() - start) * 1000),
                "error": error,
            }
        )


runtime._fetch = _capturing_fetch  # type: ignore[assignment]


def _classify(sql: str) -> str:
    low = sql.lower()
    if "information_schema" in low:
        return "catalog"  # schema/table/column/comment introspection
    return "business"


def make_agent(model: str, tools, prompt: str):
    litellm.drop_params = True
    return create_agent(
        model=ChatLiteLLM(model=model, num_retries=4, temperature=0),
        tools=tools,
        system_prompt=prompt,
        checkpointer=InMemorySaver(),
    )


async def run_mode(client, mode: str, model: str, question: str, recursion: int) -> dict:
    _CAPTURE.clear()
    runtime._schema_cache.cache_clear()
    runtime._table_cache.cache_clear()

    neocarta_tools = await client.get_tools(server_name="neocarta") if mode == "with" else []
    tools, prompt = build_tools_prompt(mode, neocarta_tools, compact=False)
    agent = make_agent(model, tools, prompt)
    cfg = {"configurable": {"thread_id": f"cap|{mode}"}, "recursion_limit": recursion}

    finished = True
    messages: list = []
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]}, config=cfg
        )
        messages = result["messages"]
    except GraphRecursionError:
        finished = False
        state = await agent.aget_state(cfg)
        messages = state.values.get("messages", [])
    except Exception:  # noqa: BLE001
        finished = False
        try:
            state = await agent.aget_state(cfg)
            messages = state.values.get("messages", [])
        except Exception:  # noqa: BLE001
            messages = []

    tin, tout, calls, _tables = tally(messages)
    queries = [
        {"n": i + 1, "kind": _classify(c["sql"]), **c} for i, c in enumerate(list(_CAPTURE))
    ]
    answer = ""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            answer = m.content if isinstance(m.content, str) else str(m.content)
            break

    return {
        "mode": mode,
        "model": model,
        "finished": finished,
        "tool_calls": calls,
        "n_tool_calls": len(calls),
        "warehouse_queries": queries,
        "n_warehouse_queries": len(queries),
        "n_catalog_queries": sum(1 for q in queries if q["kind"] == "catalog"),
        "n_business_queries": sum(1 for q in queries if q["kind"] == "business"),
        "tokens_in": tin,
        "tokens_out": tout,
        "tokens_total": tin + tout,
        "cost": round(estimate_cost(model, tin, tout), 5),
        "answer": answer[:600],
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qid", default="q6")
    ap.add_argument("--model", default=os.getenv("AGENT_MODEL", "gpt-4o-mini"))
    ap.add_argument("--recursion", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "docs" / "query_log.json"))
    args = ap.parse_args()

    questions = load_questions()
    qspec = next((q for q in questions if q["id"] == args.qid), None)
    if qspec is None:
        raise SystemExit(f"No such question id: {args.qid}")
    question = " ".join(qspec["prompt"].split())

    client = make_mcp_client()
    print(f"Question {args.qid}: {question}")
    print(f"Model: {args.model}\n")

    out: dict = {"qid": args.qid, "question": question, "catalog": catalog(), "runs": {}}
    for mode in ("without", "with"):
        print(f"== running mode={mode} ...", flush=True)
        rec = await run_mode(client, mode, args.model, question, args.recursion)
        out["runs"][mode] = rec
        print(
            f"   warehouse queries={rec['n_warehouse_queries']} "
            f"(catalog={rec['n_catalog_queries']}, business={rec['n_business_queries']}) "
            f"tool_calls={rec['n_tool_calls']} total_tokens={rec['tokens_total']:,} "
            f"{'finished' if rec['finished'] else 'DID NOT FINISH'}",
            flush=True,
        )

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
