"""Interactive Text2SQL agent — WITH vs WITHOUT the Neo4j Neocarta semantic layer.

Run the SAME question in each mode:

    uv run python src/agent.py --mode without
    uv run python src/agent.py --mode with
    uv run python src/agent.py --mode with --compact

Sweep (models × questions × modes):

    uv run python src/agent.py --runall --compact
    uv run python src/agent.py --runall --all-modes --model gpt-4o-mini
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import litellm
import tiktoken
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_litellm import ChatLiteLLM
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env", override=True)

litellm.suppress_debug_info = True

from runtime import (  # noqa: E402
    MODEL_MENU,
    available_models,
    build_tools_prompt,
    catalog,
    demo_prompts,
    estimate_cost,
    load_questions,
    make_mcp_client,
    neo4j_database,
    score_tables,
    short_model,
    tally,
)

try:
    from litellm.exceptions import RateLimitError
except Exception:  # noqa: BLE001
    RateLimitError = ()  # type: ignore[assignment]

STDERR_LOG: str | None = None
if os.getenv("DEMO_QUIET", "1").lower() not in {"0", "false", "no"}:
    STDERR_LOG = os.getenv("DEMO_STDERR_LOG", "/tmp/databricks_neocarta_agent.stderr.log")
    _errlog_fh = open(STDERR_LOG, "w", buffering=1)  # noqa: SIM115
    os.dup2(_errlog_fh.fileno(), 2)

RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "40"))
NUM_RETRIES = int(os.getenv("NUM_RETRIES", "8"))
_ENC = tiktoken.get_encoding("o200k_base")


def make_agent(model: str, tools: list[BaseTool], prompt: str):
    litellm.drop_params = True  # newer Anthropic models reject temperature=0
    return create_agent(
        model=ChatLiteLLM(model=model, num_retries=NUM_RETRIES, temperature=0),
        tools=tools,
        system_prompt=prompt,
        checkpointer=InMemorySaver(),
    )


def _toklen(x) -> int:
    return len(_ENC.encode(x if isinstance(x, str) else str(x)))


def print_trace(messages: list) -> None:
    print("\n TOKEN TRACE — where the tokens go (content size via o200k_base):")
    print(f" {'#':>2} {'role':<10}{'content':>9} detail")
    tool_total = 0
    for i, msg in enumerate(messages, 1):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        ctok = _toklen(content)
        role, detail = msg.type, ""
        if isinstance(msg, AIMessage):
            role = "ai"
            tcs = msg.tool_calls or []
            if tcs:
                detail = "-> " + ", ".join(tc["name"] for tc in tcs)
            um = getattr(msg, "usage_metadata", None)
            if um:
                detail += f" [model in={um.get('input_tokens', 0):,} out={um.get('output_tokens', 0):,}]"
        elif msg.type == "tool":
            detail = f"{getattr(msg, 'name', 'tool')} RESULT"
            tool_total += ctok
        elif msg.type == "system":
            detail = "system prompt"
        elif msg.type == "human":
            detail = "question"
        flag = " <== bulk" if (msg.type == "tool" and ctok > 3000) else ""
        print(f" {i:>2} {role:<10}{ctok:>9,} {detail}{flag}")
    ai_msgs = [m for m in messages if isinstance(m, AIMessage) and getattr(m, "usage_metadata", None)]
    ins = sum(m.usage_metadata.get("input_tokens", 0) for m in ai_msgs)
    outs = sum(m.usage_metadata.get("output_tokens", 0) for m in ai_msgs)
    print(f"\n tool results returned once : {tool_total:,} tok")
    print(f" billed model INPUT total : {ins:,} tok across {len(ai_msgs)} model call(s)")
    print(f" billed model OUTPUT total : {outs:,} tok")
    print(" -> a big tool result is re-sent as input on every later model call.\n")


def choose_model(default: str) -> str:
    available = [(mid, label) for mid, label, keyvar in MODEL_MENU if os.getenv(keyvar)]
    if not available:
        return default
    print("\nChoose an LLM for the agent:")
    for i, (_mid, label) in enumerate(available, 1):
        print(f" {i}. {label}")
    default_idx = next((i for i, (mid, _) in enumerate(available, 1) if mid == default), 1)
    while True:
        sel = input(f"Model [{default_idx}]: ").strip() or str(default_idx)
        if sel.isdigit() and 1 <= int(sel) <= len(available):
            return available[int(sel) - 1][0]
        print(" invalid choice, try again.")


async def run_silent(agent, question: str, config: dict) -> tuple[list, bool]:
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]}, config=config
        )
        return result["messages"], True
    except Exception:  # noqa: BLE001
        try:
            state = await agent.aget_state(config)
            return state.values.get("messages", []), False
        except Exception:  # noqa: BLE001
            return [], False


def print_runall_table(rows: list[dict], questions: list[dict]) -> None:
    print("\n" + "=" * 110)
    print(" PER-QUESTION RESULTS")
    print("=" * 110)
    hdr = (
        f" {'MODEL':<18}{'MODE':<12}{'Q':<4}{'IN':>9}{'OUT':>7}{'TOTAL':>9}"
        f"{'COST':>10}{'CALLS':>6}{'OK':>4}{'HIT':>5} TABLES"
    )
    print(hdr)
    print(" " + "-" * (len(hdr) + 20))
    by_id = {q["id"]: q for q in questions}
    for r in rows:
        tbl = ",".join(r["tables"]) or "-"
        qspec = by_id.get(r["qid"], {})
        hit = score_tables(set(r["tables"]), qspec) if r["tables"] else False
        print(
            f" {r['model']:<18}{r['mode']:<12}{r['qid']:<4}{r['in']:>9,}{r['out']:>7,}"
            f"{r['total']:>9,}{('$' + format(r['cost'], '.4f')):>10}{r['calls']:>6}"
            f"{('Y' if r['ok'] else 'N'):>4}{('Y' if hit else 'N'):>5} {tbl[:40]}"
        )

    print("\n" + "=" * 110)
    print(" SUMMARY — average per question (compare modes within a model row)")
    print("=" * 110)
    modes = []
    for r in rows:
        if r["mode"] not in modes:
            modes.append(r["mode"])
    model_names: list[str] = []
    for r in rows:
        if r["model"] not in model_names:
            model_names.append(r["model"])

    header = f" {'MODEL':<18}" + "".join(f"{m + ' tok':>14}" for m in modes)
    header += "".join(f"{m + ' OK':>10}" for m in modes)
    header += "".join(f"{m + ' HIT':>10}" for m in modes)
    print(header)

    def avg(lst, key):
        return sum(x[key] for x in lst) / len(lst) if lst else 0

    for model in model_names:
        line = f" {model:<18}"
        for mode in modes:
            subset = [r for r in rows if r["model"] == model and r["mode"] == mode]
            line += f"{avg(subset, 'total'):>14,.0f}"
        for mode in modes:
            subset = [r for r in rows if r["model"] == model and r["mode"] == mode]
            ok = f"{sum(1 for x in subset if x['ok'])}/{len(subset)}"
            line += f"{ok:>10}"
        for mode in modes:
            subset = [r for r in rows if r["model"] == model and r["mode"] == mode]
            hits = 0
            for x in subset:
                qspec = by_id.get(x["qid"], {})
                hits += int(score_tables(set(x["tables"]), qspec))
            line += f"{f'{hits}/{len(subset)}':>10}"
        print(line)
    print("\n OK = finished without step-limit / provider error.")
    print(" HIT = generated SQL used the expected gold/silver tables.")
    print(" Token counts across providers use different tokenizers — compare WITHIN a row.\n")


async def run_all(client, args) -> None:
    models = [args.model] if args.model else available_models()
    if not models:
        print("No models available. Set OPENAI_API_KEY / ANTHROPIC_API_KEY.")
        return
    questions = load_questions()
    prompts = demo_prompts()
    modes: list[tuple[str, bool, str]]
    if args.all_modes:
        modes = [("without", False, "without"), ("with", False, "with"), ("with", True, "compact")]
    elif args.compact:
        modes = [("without", False, "without"), ("with", True, "compact")]
    else:
        modes = [("without", False, "without"), ("with", False, "with")]

    neocarta_tools = await client.get_tools(server_name="neocarta")
    print("\n" + "=" * 80)
    print(" RUN-ALL SWEEP: models x questions x modes")
    print(f" MODELS : {[short_model(m) for m in models]}")
    print(f" QUESTIONS: {len(questions)} GRAPH: `{neo4j_database()}` CATALOG: {catalog()}")
    print(f" MODES : {[label for *_, label in modes]}")
    print(f" TOTAL RUNS: {len(models) * len(modes) * len(questions)}")
    if STDERR_LOG:
        print(f" (server/driver logs -> {STDERR_LOG})")
    print("=" * 80)

    rows: list[dict] = []
    for model in models:
        for mode, compact, label in modes:
            tools, prompt = build_tools_prompt(mode, neocarta_tools, compact=compact)
            agent = make_agent(model, tools, prompt)
            for qi, (qspec, question) in enumerate(zip(questions, prompts, strict=True), 1):
                cfg = {
                    "configurable": {"thread_id": f"{short_model(model)}|{label}|{qi}"},
                    "recursion_limit": RECURSION_LIMIT,
                }
                print(f" .. {short_model(model):<18}{label:<10}{qspec['id']:<4} ", end="", flush=True)
                msgs, finished = await run_silent(agent, question, cfg)
                tin, tout, calls, tables = tally(msgs)
                cost = estimate_cost(model, tin, tout)
                rows.append(
                    {
                        "model": short_model(model),
                        "mode": label,
                        "qid": qspec["id"],
                        "in": tin,
                        "out": tout,
                        "total": tin + tout,
                        "cost": cost,
                        "calls": len(calls),
                        "tables": sorted(tables),
                        "ok": finished,
                    }
                )
                print(
                    f"-> total={tin + tout:>7,} calls={len(calls)} "
                    f"{'ok' if finished else 'FAIL'}",
                    flush=True,
                )
    print_runall_table(rows, questions)


def banner_for(mode: str, compact: bool) -> str:
    if mode != "with":
        return "WITHOUT Neocarta (brute-force discovery)"
    if compact:
        return "WITH Neocarta (COMPACT retrieval)"
    return "WITH Neocarta semantic layer"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["with", "without"], default="with")
    parser.add_argument("--model", default=None, help="LiteLLM model id.")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Wrap Neocarta retrieval in a compact schema payload (WITH mode).",
    )
    parser.add_argument(
        "--all-modes",
        action="store_true",
        help="With --runall, sweep without + with-raw + with-compact.",
    )
    parser.add_argument("--no-trace", dest="trace", action="store_false")
    parser.add_argument("--runall", action="store_true")
    parser.set_defaults(trace=True)
    args = parser.parse_args()

    client = make_mcp_client()
    if args.runall:
        await run_all(client, args)
        return

    model = args.model or choose_model(default=os.getenv("AGENT_MODEL", "gpt-4o-mini"))
    compact = args.compact and args.mode == "with"
    neocarta_tools = await client.get_tools(server_name="neocarta") if args.mode == "with" else []
    tools, prompt = build_tools_prompt(args.mode, neocarta_tools, compact=compact)
    agent = make_agent(model, tools, prompt)
    questions = demo_prompts()
    banner = banner_for(args.mode, compact)

    print("\n" + "=" * 80)
    print(f" MODE : {banner}")
    print(f" MODEL: {model}")
    print(f" GRAPH: Neo4j `{neo4j_database()}` | CATALOG: {catalog()}")
    print(f" TOOLS: {[t.name for t in tools]}")
    if STDERR_LOG:
        print(f" (logs -> {STDERR_LOG}; set DEMO_QUIET=0 to show on screen)")
    print("=" * 80)
    print("\nDemo questions:")
    for i, q in enumerate(questions, 1):
        print(f" {i}. {q}")
    print("\nType a question (or a number), 'exit' to quit.\n")

    turn = 0
    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in {"exit", "quit", "q"}:
            break
        if not user_input:
            continue
        if user_input.isdigit() and 1 <= int(user_input) <= len(questions):
            user_input = questions[int(user_input) - 1]
            print(f"> {user_input}")

        turn += 1
        config = {"configurable": {"thread_id": f"t{turn}"}, "recursion_limit": RECURSION_LIMIT}
        finished = True
        last_messages: list = []
        try:
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                stream_mode="values",
                config=config,
            ):
                last_messages = chunk["messages"]
                latest = last_messages[-1]
                if latest.type != "ai":
                    continue
                if getattr(latest, "tool_calls", None):
                    print(" -> calling tools:", ", ".join(tc["name"] for tc in latest.tool_calls))
                if latest.content:
                    print(f"\nAgent: {latest.content}\n")
        except GraphRecursionError:
            finished = False
            state = await agent.aget_state(config)
            last_messages = state.values.get("messages", [])
            print(
                f"\n[!] Agent hit the {RECURSION_LIMIT}-step limit and gave up "
                "(it kept scanning metadata).\n"
            )
        except RateLimitError:
            finished = False
            state = await agent.aget_state(config)
            last_messages = state.values.get("messages", [])
            print(f"\n[!] Hit the model provider TPM cap mid-run ({model}).\n")
        except Exception as exc:  # noqa: BLE001
            finished = False
            try:
                state = await agent.aget_state(config)
                last_messages = state.values.get("messages", [])
            except Exception:  # noqa: BLE001
                pass
            print(f"\n[!] Run failed: {type(exc).__name__}: {str(exc)[:160]}\n")

        tin, tout, calls, tables = tally(last_messages)
        cost = estimate_cost(model, tin, tout)
        print("-" * 80)
        print(
            f" [{banner} | LLM={model}] tokens in={tin:,} out={tout:,} "
            f"total={tin + tout:,} est_cost=${cost:.4f}"
        )
        print(
            f" tool_calls={len(calls)} tables_queried={sorted(tables) or '(none)'} "
            f"{'finished' if finished else 'DID NOT FINISH'}"
        )
        print("-" * 80 + "\n")
        if args.trace and last_messages:
            print_trace(last_messages)


if __name__ == "__main__":
    asyncio.run(main())
