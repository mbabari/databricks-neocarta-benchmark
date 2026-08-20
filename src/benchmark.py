"""Benchmark token usage / cost / accuracy: WITH vs WITHOUT Neocarta.

Mirrors the Census reference benchmark
(https://github.com/mbabari/census-neocarta-benchmark):

  * ONE persistent Neocarta MCP subprocess for the whole sweep — the tools are
    bound to a live stdio session, so a retrieval never re-spawns `neocarta-mcp`.
  * Quiet terminal — the MCP subprocess and Neo4j driver write verbose Cypher /
    GqlStatusObject notifications and an Authlib deprecation warning to stderr;
    those look alarming but are NOT failures, so we redirect this process's fd 2
    to a log file (the child inherits it). Set DEMO_QUIET=0 to keep it on screen.
  * A clean census-style summary: WITHOUT tok / WITH tok / SAVING / $ / OK / HIT.

Two or three agents answer the same Databricks lakehouse questions:

  * WITHOUT — list_schemas / list_tables / get_table_columns + execute_sql
  * WITH    — Neocarta hybrid search + execute_sql
  * COMPACT — same retrieval, compacted schema payload

Usage (from repo root):

    uv run python src/benchmark.py
    MODELS=gpt-4o-mini,gpt-4o MONTHLY_QUERIES=10000 uv run python src/benchmark.py
    MODES=without,with uv run python src/benchmark.py
    MODES=without,compact uv run python src/benchmark.py
"""

from __future__ import annotations

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

import litellm  # noqa: E402

litellm.suppress_debug_info = True
# Newer Anthropic models (opus-4.8, fable-5) only accept temperature=1;
# drop unsupported params instead of erroring.
litellm.drop_params = True

# Keep the demo terminal clean. The neocarta MCP subprocess and the Neo4j driver
# write verbose notifications/warnings to stderr (the vector-search Cypher, the
# GqlStatusObject position info, fastmcp's AuthlibDeprecationWarning) that look
# alarming to a customer but are NOT failures. langchain_mcp_adapters gives no
# hook to redirect the child's stderr, so we redirect this process's stderr
# (fd 2 -> a log file), which the child inherits. All benchmark output uses
# print() -> stdout and is unaffected. Set DEMO_QUIET=0 to keep stderr on screen.
STDERR_LOG: str | None = None
if os.getenv("DEMO_QUIET", "1").lower() not in {"0", "false", "no"}:
    STDERR_LOG = os.getenv("DEMO_STDERR_LOG", "/tmp/databricks_neocarta_benchmark.stderr.log")
    _errlog_fh = open(STDERR_LOG, "w", buffering=1)  # noqa: SIM115
    os.dup2(_errlog_fh.fileno(), 2)

from langchain.tools import BaseTool  # noqa: E402
from langchain_litellm import ChatLiteLLM  # noqa: E402
from langchain_mcp_adapters.tools import load_mcp_tools  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from runtime import (  # noqa: E402
    available_models,
    build_tools_prompt,
    catalog,
    demo_prompts,
    estimate_cost,
    final_answer_text,
    load_questions,
    make_mcp_client,
    neo4j_database,
    score_answer,
    score_tables,
    short_model,
    tally,
)

RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "40"))
MONTHLY_QUERIES = int(os.getenv("MONTHLY_QUERIES", "10000"))
NUM_RETRIES = int(os.getenv("NUM_RETRIES", "4"))


def _models() -> list[str]:
    raw = os.getenv("MODELS")
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return available_models() or ["gpt-4o-mini"]


def _modes() -> list[tuple[str, bool, str]]:
    raw = os.getenv("MODES", "without,with")
    wanted = [m.strip() for m in raw.split(",") if m.strip()]
    mapping = {
        "without": ("without", False, "without"),
        "with": ("with", False, "with"),
        "compact": ("with", True, "compact"),
    }
    out = [mapping[k] for k in ("without", "with", "compact") if k in wanted]
    return out or [mapping["without"], mapping["with"]]


def make_agent(model: str, tools: list[BaseTool], prompt: str):
    from langchain.agents import create_agent

    return create_agent(
        model=ChatLiteLLM(
            model=model,
            num_retries=NUM_RETRIES,
            temperature=0,
            # Cap each provider call so an overloaded API can't stall the sweep
            # for many minutes (retries still apply on timeout).
            request_timeout=int(os.getenv("LLM_TIMEOUT", "90")),
        ),
        tools=tools,
        system_prompt=prompt,
        checkpointer=InMemorySaver(),
    )


async def run_silent(agent, question: str, config: dict) -> tuple[list, bool, str]:
    """Run one question without streaming. Returns (messages, finished, error).

    Any step-limit / provider error is swallowed so a sweep never aborts; partial
    messages are recovered from the checkpointer so tokens burned still count.
    `error` is a short reason ('' when the run finished) shown next to FAIL rows.
    """
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]}, config=config
        )
        return result["messages"], True, ""
    except Exception as exc:  # noqa: BLE001 — GraphRecursionError, RateLimitError, provider errors
        if os.getenv("DEMO_DEBUG"):
            import traceback

            traceback.print_exc()
        if _is_quota_error(exc):
            err = "OPENAI OVER QUOTA"
        else:
            err = type(exc).__name__
        try:
            state = await agent.aget_state(config)
            return state.values.get("messages", []), False, err
        except Exception:  # noqa: BLE001
            return [], False, err


async def run_sweep(neocarta_tools: list, models: list[str], modes) -> list[dict]:
    questions = load_questions()
    prompts = demo_prompts()
    by_id = {q["id"]: q for q in questions}
    mode_tools = {
        label: build_tools_prompt(mode, neocarta_tools, compact=compact)
        for mode, compact, label in modes
    }

    print("\n" + "=" * 80)
    print(" RUN-ALL SWEEP: models x questions x modes")
    print(f" MODELS   : {[short_model(m) for m in models]}")
    print(f" QUESTIONS: {len(questions)}  GRAPH: `{neo4j_database()}`  CATALOG: {catalog()}")
    print(f" MODES    : {[label for *_, label in modes]}")
    print(f" TOTAL RUNS: {len(models) * len(modes) * len(questions)}")
    if STDERR_LOG:
        print(f" (server/driver logs -> {STDERR_LOG}; set DEMO_QUIET=0 to show on screen)")
    print("=" * 80)

    rows: list[dict] = []
    for model in models:
        for mode, compact, label in modes:
            tools, prompt = mode_tools[label]
            agent = make_agent(model, tools, prompt)
            for qi, (qspec, question) in enumerate(zip(questions, prompts, strict=True), 1):
                cfg = {
                    "configurable": {"thread_id": f"{short_model(model)}|{label}|{qi}"},
                    "recursion_limit": RECURSION_LIMIT,
                }
                print(f" .. {short_model(model):<18}{label:<10}{qspec['id']:<4} ", end="", flush=True)
                t0 = time.perf_counter()
                msgs, finished, err = await run_silent(agent, question, cfg)
                secs = time.perf_counter() - t0
                tin, tout, calls, tables = tally(msgs)
                cost = estimate_cost(model, tin, tout)
                hit = finished and score_tables(tables, qspec)
                answer = final_answer_text(msgs)
                ans_ok = score_answer(answer, qspec)
                if ans_ok is not None:
                    ans_ok = finished and ans_ok
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
                        "secs": round(secs, 1),
                        "tables": sorted(tables),
                        "ok": finished,
                        "error": err,
                        "hit": hit,
                        "answer_ok": ans_ok,
                        "answer": answer[:300],
                    }
                )
                ans_str = "-" if ans_ok is None else ("ANS✓" if ans_ok else "ans✗")
                fail_note = "" if finished else f"  [{err}]"
                print(
                    f"-> total={tin + tout:>7,}  calls={len(calls)}  {secs:>5.1f}s  "
                    f"{'ok' if finished else 'FAIL'}  {'HIT' if hit else 'miss'}  {ans_str}{fail_note}",
                    flush=True,
                )
    print_summary(rows, by_id, modes)
    return rows


# Display labels for the printed tables (results.json keeps the raw keys).
DISPLAY_LABELS = {
    "without": "without SL",
    "with": "🧠 with Neo4j SL",
    "compact": "🧠 with Neo4j SL",
}


def _disp(label: str) -> str:
    return DISPLAY_LABELS.get(label, label)


def _acc_icon(hits: int, total: int) -> str:
    """Green when every question hit the right table, red when none did."""
    if total == 0:
        return "  -"
    frac = hits / total
    if frac >= 0.999:
        return "🟢"
    if frac >= 0.5:
        return "🟡"
    return "🔴"


def print_summary(rows: list[dict], by_id: dict, modes) -> None:
    labels = [label for *_, label in modes]
    models: list[str] = []
    for r in rows:
        if r["model"] not in models:
            models.append(r["model"])

    print("\n" + "=" * 104)
    print(" PER-QUESTION RESULTS")
    print("=" * 104)
    hdr = (
        f" {'MODEL':<18}{'MODE':<18}{'Q':<4}{'IN':>9}{'OUT':>7}{'TOTAL':>9}"
        f"{'COST':>10}{'CALLS':>6}{'TIME':>7}{'OK':>4}{'TBL':>5}{'ANS':>5}  TABLES"
    )
    print(hdr)
    print(" " + "-" * (len(hdr) + 8))
    for r in rows:
        tbl = (",".join(r["tables"]) or "-")[:42]
        acc = "🟢" if r["hit"] else "🔴"
        ans_ok = r.get("answer_ok")
        ans = " -" if ans_ok is None else ("🟢" if ans_ok else "🔴")
        print(
            f" {r['model']:<18}{_disp(r['mode']):<18}{r['qid']:<4}{r['in']:>9,}{r['out']:>7,}"
            f"{r['total']:>9,}{('$' + format(r['cost'], '.4f')):>10}{r['calls']:>6}"
            f"{format(r.get('secs', 0), '.1f') + 's':>7}{('Y' if r['ok'] else 'N'):>4}{acc:>4}{ans:>4}  {tbl}"
        )

    def avg(subset, key):
        return sum(x[key] for x in subset) / len(subset) if subset else 0

    print("\n" + "=" * 104)
    print(" SUMMARY — average per question (WITHOUT vs WITH the Neo4j semantic layer)")
    print("=" * 104)
    sh = (
        f" {'MODEL':<18}{'MODE':<18}{'tok':>9}{'$/q':>10}{'SAVING':>8}{'TIME/q':>8}"
        f"{'OK':>7}{'$/mo @' + format(MONTHLY_QUERIES, ','):>14}  {'TABLES':<10}{'ANSWERS':<10}"
    )
    print(sh)
    print(" " + "-" * (len(sh) + 2))
    for m in models:
        base = [r for r in rows if r["model"] == m and r["mode"] == "without"]
        base_tok = avg(base, "total")
        for lbl in labels:
            sub = [r for r in rows if r["model"] == m and r["mode"] == lbl]
            if not sub:
                continue
            tok = avg(sub, "total")
            cost = avg(sub, "cost")
            secs = avg(sub, "secs")
            hits = sum(1 for x in sub if x["hit"])
            scored = [x for x in sub if x.get("answer_ok") is not None]
            ans_hits = sum(1 for x in scored if x["answer_ok"])
            ok = f"{sum(1 for x in sub if x['ok'])}/{len(sub)}"
            if lbl == "without" or not base_tok:
                saving = "—"
            else:
                saving = f"{100 * (base_tok - tok) / base_tok:.0f}%"
            monthly = cost * MONTHLY_QUERIES
            tables_acc = f"{hits}/{len(sub)} {_acc_icon(hits, len(sub))}"
            answers_acc = (
                f"{ans_hits}/{len(scored)} {_acc_icon(ans_hits, len(scored))}" if scored else "-"
            )
            print(
                f" {m:<18}{_disp(lbl):<18}{tok:>9,.0f}{('$' + format(cost, '.4f')):>10}"
                f"{saving:>8}{format(secs, '.1f') + 's':>8}{ok:>7}{monthly:>14,.2f}"
                f"  {tables_acc:<10}{answers_acc:<10}"
            )

    print("\n SAVING  = token reduction vs the same model WITHOUT the semantic layer.")
    print(" TIME/q  = average wall-clock seconds to answer one question in that mode.")
    print(" $/mo    = projected monthly spend for that mode at the query volume above.")
    print(" OK      = questions that finished without hitting the step limit / a provider error.")
    print(" TABLES  = SQL used the expected gold/silver tables (provenance).")
    print(" ANSWERS = final answer contained the expected ground-truth values from questions.yaml.")
    print(" Icons: 🟢 all  🟡 some  🔴 none.")
    print(" Token counts across providers use different tokenizers — compare WITHIN a model.\n")


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "insufficient_quota" in msg or "no credits" in msg or "credit_balance" in msg


def preflight(models: list[str], modes) -> list[str]:
    """Fail fast with a clear message instead of sweeping against a dead provider.

    Probes each provider once (1-token completion) and the embedding endpoint
    (needed by every WITH-mode retrieval). Over-quota OpenAI drops the GPT
    models from the sweep; a dead embedding endpoint aborts WITH modes.
    """
    providers = {"openai" if "/" not in m else m.split("/")[0] for m in models}
    probe_model = {"openai": "gpt-4o-mini", "anthropic": "anthropic/claude-haiku-4-5-20251001"}
    dead: set[str] = set()
    for prov in sorted(providers):
        try:
            litellm.completion(
                model=probe_model.get(prov, next(m for m in models if m.startswith(prov))),
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                num_retries=0,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            reason = "over quota (add credits)" if _is_quota_error(exc) else str(exc)[:120]
            print(f"\n *** {prov} is unavailable: {reason}")
            print(f" *** Skipping models: {[m for m in models if (prov == 'openai') == ('/' not in m)]}")
            dead.add(prov)
    models = [m for m in models if ("openai" if "/" not in m else m.split("/")[0]) not in dead]
    if not models:
        raise SystemExit(" *** No usable models left — aborting.")

    if any(mode == "with" for mode, _, _ in modes):
        emb_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        kwargs = {"api_base": os.getenv("EMBEDDING_API_BASE")} if os.getenv("EMBEDDING_API_BASE") else {}
        try:
            litellm.embedding(model=emb_model, input=["ping"], num_retries=0, timeout=30, **kwargs)
        except Exception as exc:  # noqa: BLE001
            reason = "OpenAI over quota (add credits)" if _is_quota_error(exc) else str(exc)[:160]
            raise SystemExit(
                f"\n *** Embeddings ({emb_model}) are unavailable: {reason}\n"
                " *** Every WITH-mode retrieval embeds the query, so the sweep would fail.\n"
                " *** Fix the provider, or use the local embedding server:\n"
                " ***   1. uv run python src/local_embeddings.py   (keep it running)\n"
                " ***   2. .env: EMBEDDING_MODEL=openai/all-mpnet-base-v2\n"
                " ***           EMBEDDING_API_BASE=http://127.0.0.1:8876/v1\n"
                " ***   3. re-embed once: uv run python src/build_semantic_layer.py --only embeddings\n"
            )
    return models


async def async_main() -> None:
    models = _models()
    modes = _modes()
    models = preflight(models, modes)
    needs_neocarta = any(mode == "with" for mode, _, _ in modes)

    client = make_mcp_client()
    if needs_neocarta:
        # One persistent stdio session -> one neocarta-mcp subprocess reused for
        # every retrieval across all models/questions (no per-call re-spawn).
        async with client.session("neocarta") as session:
            neocarta_tools = await load_mcp_tools(session)
            rows = await run_sweep(neocarta_tools, models, modes)
    else:
        rows = await run_sweep([], models, modes)

    out = ROOT / "eval" / "results.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f" Wrote {out}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    asyncio.run(async_main())
