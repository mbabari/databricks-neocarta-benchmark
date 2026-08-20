"""Compact a Neocarta TableContext payload before it reaches the LLM.

Same retrieval, far fewer tokens: drop null/empty JSON fields and emit a terse
`name type` column list (keep FK refs; optional column descriptions).
"""

from __future__ import annotations

import json
import os

INCLUDE_COL_DESCRIPTIONS = os.getenv("COMPACT_DESCRIPTIONS", "0").lower() in {
    "1",
    "true",
    "yes",
}
COMPACT_MAX_TABLES = int(os.getenv("COMPACT_MAX_TABLES", "3"))


def extract_json_text(raw) -> str:
    """Neocarta tool results arrive as a JSON string or a list of content blocks."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(raw)


def compact_table_context(raw) -> str:
    """Strip null/empty boilerplate from a Neocarta TableContext payload."""
    text = extract_json_text(raw)
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return text
    tables = data if isinstance(data, list) else [data]
    if not tables:
        return "No matching table found. Refine the layer/year/region/measure terms."
    blocks: list[str] = []
    for table in tables:
        cols = table.get("columns", []) or []
        parts: list[str] = []
        for col in cols:
            name = col.get("column_name")
            if not name:
                continue
            piece = f"{name} {col.get('data_type') or ''}".strip()
            desc = col.get("column_description")
            if INCLUDE_COL_DESCRIPTIONS and desc:
                piece += f" ({desc})"
            refs = col.get("references") or []
            if refs:
                piece += f" -> {','.join(refs)}"
            parts.append(piece)
        schema = table.get("schema_name") or table.get("schema") or ""
        db = table.get("database_name") or table.get("database") or ""
        prefix = ".".join(p for p in (db, schema, table.get("table_name")) if p)
        blocks.append(
            f"TABLE {prefix} — {table.get('table_description', '')}\n"
            f"  {len(parts)} columns: " + ", ".join(parts)
        )
    return "\n\n".join(blocks)
