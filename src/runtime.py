"""Shared Databricks connection, tools, prompts, and scoring for the demo agents."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.tools import BaseTool

ROOT = Path(__file__).resolve().parents[1]

NEOCARTA_ALLOWED = {
    "get_context_by_table_hybrid_search",
    "get_context_by_column_hybrid_search",
    "get_context_by_schema_and_table_vector_search",
}

NEOCARTA_TABLE_TOOL = "get_context_by_table_hybrid_search"

MODEL_MENU = [
    ("gpt-4o-mini", "OpenAI gpt-4o-mini", "OPENAI_API_KEY"),
    ("gpt-4o", "OpenAI gpt-4o — strong, pricier", "OPENAI_API_KEY"),
    (
        "anthropic/claude-haiku-4-5-20251001",
        "Anthropic Claude Haiku 4.5 — cheap & fast",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-sonnet-4-5-20250929",
        "Anthropic Claude Sonnet 4.5 — strong, balanced",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-opus-4-5-20251101",
        "Anthropic Claude Opus 4.5 — most capable, pricier",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-sonnet-4-6",
        "Anthropic Claude Sonnet 4.6 — newer Sonnet",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-opus-4-8",
        "Anthropic Claude Opus 4.8 — newer Opus",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-sonnet-5",
        "Anthropic Claude Sonnet 5 — latest Sonnet",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-opus-5",
        "Anthropic Claude Opus 5 — latest Opus",
        "ANTHROPIC_API_KEY",
    ),
    (
        "anthropic/claude-fable-5",
        "Anthropic Claude Fable 5 — latest generation",
        "ANTHROPIC_API_KEY",
    ),
]


def catalog() -> str:
    return os.getenv("DATABRICKS_CATALOG", "neocarta_demo")


def neo4j_database() -> str:
    return os.getenv("NEO4J_DATABASE", "neo4j")


def load_questions() -> list[dict]:
    path = ROOT / "eval" / "questions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = data["questions"]
    max_q = int(os.getenv("MAX_QUESTIONS", "0"))
    if max_q:
        questions = questions[:max_q]
    return questions


def demo_prompts() -> list[str]:
    return [" ".join(q["prompt"].split()) for q in load_questions()]


def _connect():
    from databricks_auth import sql_connection

    return sql_connection()


def _fetch(sql_text: str, limit: int | None = None):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)
            rows = cur.fetchmany(limit) if limit else cur.fetchall()
            cols = [d[0] for d in (cur.description or [])]
            return [dict(zip(cols, row, strict=False)) for row in rows]


@tool
def execute_sql(sql: str) -> str:
    """Execute a Databricks SQL query and return up to 50 rows.

    Tables must be fully qualified as `catalog`.`schema`.`table` (backticks
    optional when names are lowercase identifiers). Select only the columns you need.
    """
    try:
        rows = _fetch(sql, limit=50)
        return f"OK. Rows: {rows}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


@lru_cache(maxsize=1)
def _schema_cache() -> tuple[str, ...]:
    cat = catalog()
    rows = _fetch(
        f"SELECT schema_name FROM `{cat}`.information_schema.schemata "
        f"ORDER BY schema_name"
    )
    return tuple(r["schema_name"] for r in rows)


@lru_cache(maxsize=32)
def _table_cache(schema_name: str) -> tuple[str, ...]:
    cat = catalog()
    rows = _fetch(
        f"SELECT table_name FROM `{cat}`.information_schema.tables "
        f"WHERE table_schema = '{schema_name}' ORDER BY table_name"
    )
    return tuple(r["table_name"] for r in rows)


@tool
def list_schemas() -> str:
    """List schemas in the demo Unity Catalog catalog."""
    names = _schema_cache()
    return f"Catalog `{catalog()}` schemas ({len(names)}): " + ", ".join(names)


@tool
def list_tables(schema_name: str) -> str:
    """List tables in one schema of the demo catalog."""
    names = _table_cache(schema_name)
    if not names:
        return f"No tables in schema `{schema_name}` (check the name)."
    return f"`{catalog()}.{schema_name}` tables ({len(names)}): " + ", ".join(names)


@tool
def get_table_columns(schema_name: str, table_name: str) -> str:
    """Return column names and types for one table. Pass schema and table separately."""
    cat = catalog()
    rows = _fetch(
        f"SELECT column_name, full_data_type FROM `{cat}`.information_schema.columns "
        f"WHERE table_schema = '{schema_name}' AND table_name = '{table_name}' "
        f"ORDER BY ordinal_position"
    )
    if not rows:
        return f"No such table: {schema_name}.{table_name}"
    cols = [f"{r['column_name']} {r['full_data_type']}" for r in rows]
    return f"{schema_name}.{table_name} ({len(cols)} columns): " + ", ".join(cols)


CATALOG_BLURB = """This is a legacy enterprise lakehouse: 12 schemas and ~260 tables whose
physical names are opaque codes (t_0100, a_1005, f_2003, x_ord_2024,
x_feed_com_017) that do NOT reveal business meaning. Many near-duplicate
copies of the same entity exist (raw landing shards, archives, staging,
marketing/HR homonyms). The business meaning lives only in table and column
COMMENT metadata."""


def prompt_with(*, compact: bool) -> str:
    cat = catalog()
    retrieve = (
        "Call get_table_schema_compact ONCE with the key business terms from the "
        "question (measure + entity + year/region). It returns the best-matching "
        "tables and a compact column list (name + type + foreign keys)."
        if compact
        else "Call get_context_by_table_hybrid_search ONCE with the key business "
        "terms from the question (measure + entity + year/region) and set "
        "max_tables=3 so you retrieve only the best-matching tables and their columns."
    )
    tool_name = "get_table_schema_compact" if compact else "get_context_by_table_hybrid_search"
    return f"""You are a Text2SQL agent for a Databricks Unity Catalog lakehouse.
Tables live in catalog `{cat}` and must be fully qualified as `{cat}.schema.table`.

{CATALOG_BLURB}

MANDATORY before writing SQL:
* {retrieve}
* Do not call {tool_name} repeatedly. One targeted retrieval is enough.
Then call execute_sql.

Rules:
* Trust the retrieved table descriptions to pick the table whose description
  matches the business term in the question. Use the exact physical names.
* Use Databricks SQL. Select only the columns you need.
* Give a short, readable final answer."""


def prompt_without() -> str:
    cat = catalog()
    return f"""You are a Text2SQL agent for a Databricks Unity Catalog lakehouse.
Tables live in catalog `{cat}` and must be fully qualified as `{cat}.schema.table`.

{CATALOG_BLURB}

Use list_schemas, list_tables, and get_table_columns to discover tables. Table
and column comments are available through execute_sql on
`{cat}`.information_schema.tables / columns if you need them.

Rules:
* Use Databricks SQL. Select only the columns you need.
* Give a short, readable final answer."""


def make_mcp_client() -> MultiServerMCPClient:
    env = {
        k: v
        for k, v in {
            "NEO4J_URI": os.getenv("NEO4J_URI"),
            "NEO4J_USERNAME": os.getenv("NEO4J_USERNAME"),
            "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
            "NEO4J_DATABASE": neo4j_database(),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "EMBEDDING_MODEL": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            "EMBEDDING_DIMENSIONS": os.getenv("EMBEDDING_DIMENSIONS", "768"),
            # Route ONLY the MCP server's embedding calls to a custom endpoint
            # (e.g. src/local_embeddings.py). Scoped to this subprocess so chat
            # models in the main process are unaffected.
            "OPENAI_API_BASE": os.getenv("EMBEDDING_API_BASE"),
            # Keep LiteLLM from printing colored error banners to stdout inside
            # the MCP subprocess — that corrupts the JSON-RPC stdio stream and
            # surfaces to agents as bogus "vector search unavailable" errors.
            "LITELLM_SUPPRESS_DEBUG_INFO": "true",
            "NO_COLOR": "1",
            "LITELLM_LOG": "ERROR",
        }.items()
        if v is not None
    }
    return MultiServerMCPClient(
        {
            "neocarta": {
                "transport": "stdio",
                "command": "uv",
                "args": ["run", "--directory", str(ROOT), "neocarta-mcp"],
                "env": env,
            }
        }
    )


def extract_sql_tables(sql: str) -> set[str]:
    """Return schema.table references found in generated SQL."""
    cat = re.escape(catalog())
    found: set[str] = set()
    pattern = rf"(?:`?{cat}`?\.)?`?([a-z][a-z0-9_]*)`?\.`?([a-z][a-z0-9_]*)`?"
    for schema, table in re.findall(pattern, sql, flags=re.IGNORECASE):
        found.add(f"{schema.lower()}.{table.lower()}")
    return found


def final_answer_text(messages: list) -> str:
    """Return the agent's last non-empty AI message (the final answer)."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                return content
    return ""


def score_answer(answer_text: str, question: dict) -> bool | None:
    """Check the final answer against the question's expected_answer regexes.

    All patterns must match (case-insensitive, commas/$ stripped so 324,000 ==
    $324000). Returns None when the question defines no expected_answer.
    """
    patterns = question.get("expected_answer")
    if not patterns:
        return None
    if not answer_text:
        return False
    text = answer_text.lower().replace(",", "").replace("$", "")
    return all(re.search(str(p).lower(), text) for p in patterns)


def score_tables(sql_tables: set[str], question: dict) -> bool:
    expected = {t.lower() for t in question.get("expected_tables") or []}
    if expected and expected.issubset(sql_tables):
        return True
    join = {t.lower() for t in question.get("accepted_join") or []}
    if join and join.issubset(sql_tables):
        return True
    any_ok = {t.lower() for t in question.get("accepted_any") or []}
    if any_ok and sql_tables & any_ok:
        return True
    return False


def available_models() -> list[str]:
    return [mid for mid, _, keyvar in MODEL_MENU if os.getenv(keyvar)]


def short_model(model_id: str) -> str:
    return re.sub(r"-\d{8}$", "", model_id.split("/")[-1])


def make_compact_retrieval_tool(neocarta_table_tool: BaseTool) -> BaseTool:
    from compact import COMPACT_MAX_TABLES, compact_table_context

    async def _retrieve(text_content: str) -> str:
        raw = await neocarta_table_tool.ainvoke(
            {"text_content": text_content, "max_tables": COMPACT_MAX_TABLES}
        )
        return compact_table_context(raw)

    return StructuredTool.from_function(
        coroutine=_retrieve,
        name="get_table_schema_compact",
        description=(
            "Find the best-matching Unity Catalog tables for the question and "
            "return names plus a COMPACT column list (name + type + foreign keys). "
            "Pass key terms (gold/silver layer + measure + year/region) as "
            "text_content. Call this exactly once."
        ),
    )


def build_tools_prompt(
    mode: str,
    neocarta_tools: list,
    *,
    compact: bool,
) -> tuple[list[BaseTool], str]:
    if mode == "with":
        if compact:
            raw = next((t for t in neocarta_tools if t.name == NEOCARTA_TABLE_TOOL), None)
            if raw is None:
                raise RuntimeError(
                    f"Neocarta tool '{NEOCARTA_TABLE_TOOL}' not registered. "
                    "Is the graph built with Table vector + full-text indexes?"
                )
            tools: list[BaseTool] = [make_compact_retrieval_tool(raw), execute_sql]
        else:
            tools = [t for t in neocarta_tools if t.name in NEOCARTA_ALLOWED] + [execute_sql]
        prompt = prompt_with(compact=compact)
    else:
        tools = [list_schemas, list_tables, get_table_columns, execute_sql]
        prompt = prompt_without()
    for item in tools:
        item.handle_tool_error = True
    return tools, prompt


def tally(messages: list):
    tin = tout = 0
    calls: list[str] = []
    tables: set[str] = set()
    for msg in messages:
        um = getattr(msg, "usage_metadata", None)
        if um:
            tin += um.get("input_tokens", 0)
            tout += um.get("output_tokens", 0)
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                calls.append(tc["name"])
                if tc["name"] == "execute_sql":
                    tables |= extract_sql_tables(tc["args"].get("sql", ""))
    return tin, tout, calls, tables


def estimate_cost(model: str, tin: int, tout: int) -> float:
    try:
        from litellm import cost_per_token

        pin, pout = cost_per_token(model=model, prompt_tokens=tin, completion_tokens=tout)
        return pin + pout
    except Exception:  # noqa: BLE001
        return 0.0
