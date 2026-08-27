"""Render the README / article diagrams from the live spec and results.

Usage:
    uv run python src/make_diagrams.py            # writes docs/*.png
    uv run python src/make_diagrams.py --results  # token + cost charts
    uv run python src/make_diagrams.py --cost     # cost chart only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse import build_lakehouse  # noqa: E402

DOCS = ROOT / "docs"

LAYERS = [
    (
        "LANDING (raw, decoys)",
        "#fdecea",
        "#c0392b",
        ["lnd_sls_raw", "lnd_svc_raw", "lnd_fin_raw"],
        "Year/region shards, duplicate entity dumps, wide unlabelled feed extracts",
    ),
    (
        "ODS (cleaned, system of record)",
        "#fef9e7",
        "#b7950b",
        ["ods_crm_01", "ods_com_02", "ods_svc_03"],
        "Conformed CRM / commercial / support entities with declared PK-FK",
    ),
    (
        "DATA MARTS (governed, certified)",
        "#eafaf1",
        "#1e8449",
        ["dm_agg_10", "dm_fin_20"],
        "Executive aggregates: customer 360, ARR, bookings, NRR — the right answers",
    ),
    (
        "OTHER (traps)",
        "#f4f6f7",
        "#5d6d7e",
        ["arch_hist", "etl_stg", "mkt_ods", "hcm_ods"],
        "Frozen archives, nightly staging, marketing & HR homonyms",
    ),
]

SAMPLE_TABLES = {
    "lnd_sls_raw": "x_cst, x_cst_mstr, x_ord_2016..2025,\nx_ord_emea, x_feed_com_001..040",
    "lnd_svc_raw": "x_tkt, x_tkt_2016..2025,\nx_csat, x_feed_svc_001..035",
    "lnd_fin_raw": "x_inv, x_inv_2016..2025,\nx_gl_entries, x_feed_fin_001..035",
    "ods_crm_01": "t_0100 customer master\nt_0140 subscriptions",
    "ods_com_02": "t_0220 products, t_0230 orders,\nt_0231 order lines",
    "ods_svc_03": "t_0320 support tickets\n(CSAT, P1 priority)",
    "dm_agg_10": "a_1001 customer 360, a_1004 P1 subs,\na_1005 bookings, a_1007 NRR",
    "dm_fin_20": "f_2001 ARR, f_2003 rev-rec,\nf_2004 dunning, f_2005 ARR snapshot",
    "arch_hist": "h_cst, h_ord_2016..2021,\nh_cst_snap_2020..2022",
    "etl_stg": "w_cst, w_ord, w_tkt,\nw_lndg_* (truncated nightly)",
    "mkt_ods": "m_cst = audience members,\nNOT paying customers",
    "hcm_ods": "e_tkt = HR cases,\nNOT support tickets",
}


def catalog_map(tables) -> None:
    counts: dict[str, int] = {}
    cols: dict[str, int] = {}
    for t in tables:
        counts[t.schema] = counts.get(t.schema, 0) + 1
        cols[t.schema] = cols.get(t.schema, 0) + len(t.columns)

    fig, ax = plt.subplots(figsize=(16, 11), dpi=150)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12.6)
    ax.axis("off")

    ax.text(
        8, 12.35,
        "Synthetic 'legacy enterprise' lakehouse — Databricks Unity Catalog",
        ha="center", fontsize=17, fontweight="bold",
    )
    ax.text(
        8, 11.75,
        f"{len(tables)} tables, {sum(cols.values()):,} columns across 12 schemas. "
        "Physical names are opaque legacy codes — business meaning lives only in\n"
        "COMMENT metadata, which is exactly what the Neo4j semantic layer embeds and retrieves.",
        ha="center", va="center", fontsize=11.5, color="#333333",
    )

    row_y = [8.7, 5.9, 3.1, 0.3]
    row_h = 2.0
    for (label, face, edge, schemas, note), y in zip(LAYERS, row_y, strict=True):
        ax.text(0.25, y + row_h + 0.16, label, fontsize=13, fontweight="bold",
                color=edge, va="bottom")
        ax.text(15.75, y + row_h + 0.16, note, fontsize=9, color="#555555",
                ha="right", va="bottom", style="italic")
        w = 15.5 / len(schemas)
        for i, schema in enumerate(schemas):
            x = 0.25 + i * w
            box = FancyBboxPatch(
                (x + 0.08, y), w - 0.16, row_h,
                boxstyle="round,pad=0.03,rounding_size=0.08",
                facecolor=face, edgecolor=edge, linewidth=1.6,
            )
            ax.add_patch(box)
            ax.text(x + w / 2, y + row_h - 0.32, schema, ha="center",
                    fontsize=12.5, fontweight="bold", family="monospace", color=edge)
            ax.text(x + w / 2, y + row_h - 0.66,
                    f"{counts.get(schema, 0)} tables · {cols.get(schema, 0):,} columns",
                    ha="center", fontsize=9.5, color="#333333")
            ax.text(x + w / 2, y + 0.55, SAMPLE_TABLES.get(schema, ""),
                    ha="center", fontsize=8.6, family="monospace", color="#222222")

    # Flow arrows between layers (landing -> ods -> marts).
    for y_top, note_text in [(8.7, "ETL (cleaned, conformed)"), (5.9, "governed aggregation")]:
        ax.add_patch(FancyArrowPatch(
            (7.0, y_top - 0.06), (7.0, y_top - 0.52), arrowstyle="-|>",
            mutation_scale=22, linewidth=1.8, color="#7f8c8d",
        ))
        ax.text(7.25, y_top - 0.32, note_text, fontsize=9, color="#7f8c8d", va="center")

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "catalog_map.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def _eval_results_path() -> Path:
    """Prefer the published 8-model Anthropic sweep over a later overwrite of results.json."""
    preferred = ROOT / "eval" / "results-8models-anthropic.json"
    fallback = ROOT / "eval" / "results.json"
    if preferred.exists():
        return preferred
    return fallback


def _load_eval_rows() -> tuple[list[dict], list[str]]:
    rows = json.loads(_eval_results_path().read_text())
    models: list[str] = []
    for r in rows:
        if r["model"] not in models:
            models.append(r["model"])
    return rows, models


def _agg(rows: list[dict], model: str, mode: str, key: str):
    sub = [r for r in rows if r["model"] == model and r["mode"] == mode]
    if not sub:
        return 0.0, 0, 0
    val = sum(r[key] for r in sub) / len(sub)
    ans = sum(1 for r in sub if r.get("answer_ok"))
    return val, ans, len(sub)


def _saving_pct(baseline: float, with_sl: float) -> float | None:
    if not baseline:
        return None
    return 100.0 * (baseline - with_sl) / baseline


def _grouped_mode_bars(ax, rows: list[dict], models: list[str], key: str, fmt) -> None:
    xs = range(len(models))
    w = 0.38
    for off, mode, color, label in [
        (-w / 2, "without", "#c0392b", "without semantic layer"),
        (w / 2, "with", "#1e8449", "with Neo4j semantic layer"),
    ]:
        vals = [_agg(rows, m, mode, key)[0] for m in models]
        bars = ax.bar([x + off for x in xs], vals, w, color=color, alpha=0.85, label=label)
        for m, bar in zip(models, bars, strict=True):
            val, ans, n = _agg(rows, m, mode, key)
            extra = ""
            if mode == "with":
                base, _, _ = _agg(rows, m, "without", key)
                pct = _saving_pct(base, val)
                if pct is not None:
                    extra = f"\n−{pct:.0f}%"
            ax.annotate(
                f"{fmt(val)}{extra}\n{ans}/{n} ✓",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                ha="center", va="bottom", fontsize=9, fontweight="bold", color=color,
            )


def results_chart() -> None:
    rows, models = _load_eval_rows()

    fig, ax = plt.subplots(figsize=(max(13, 1.9 * len(models)), 6.8), dpi=150)
    _grouped_mode_bars(ax, rows, models, "total", lambda v: f"{v / 1000:.0f}k")

    ax.set_xticks(list(range(len(models))))
    ax.set_xticklabels(models, fontsize=10, rotation=15, ha="right")
    ax.set_ylabel("avg tokens per question")
    ax.set_title(
        "Text2SQL over a 264-table legacy lakehouse — tokens, saving %, and correct answers",
        fontsize=13, fontweight="bold",
    )
    ax.legend(fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(y=0.28)

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "benchmark_results.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def cost_chart() -> None:
    rows, models = _load_eval_rows()

    fig, ax = plt.subplots(figsize=(max(13, 1.9 * len(models)), 6.8), dpi=150)
    _grouped_mode_bars(ax, rows, models, "cost", lambda v: f"${v:.2f}")

    ax.set_xticks(list(range(len(models))))
    ax.set_xticklabels(models, fontsize=10, rotation=15, ha="right")
    ax.set_ylabel("avg $ per question")
    ax.set_title(
        "Text2SQL over a 264-table legacy lakehouse — cost, saving %, and correct answers",
        fontsize=13, fontweight="bold",
    )
    ax.legend(fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(y=0.28)

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "benchmark_cost.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def _box(ax, x, y, w, h, face, edge, lw=1.6, radius=0.08):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0.02,rounding_size={radius}",
            facecolor=face, edgecolor=edge, linewidth=lw,
        )
    )


def _arrow(ax, x0, y0, x1, y1, color="#555555"):
    ax.add_patch(
        FancyArrowPatch(
            (x0, y0), (x1, y1),
            arrowstyle="-|>", mutation_scale=16, linewidth=1.6, color=color,
        )
    )


def sl_flow() -> None:
    """Question → hybrid retrieval in Neo4j → Text2SQL on Databricks → answer."""
    fig, ax = plt.subplots(figsize=(16.5, 10.8), dpi=150)
    ax.set_xlim(0, 16.5)
    ax.set_ylim(0, 10.8)
    ax.axis("off")

    neo, dbx, llm, ink = "#0e6655", "#c0392b", "#1a5276", "#222222"
    neo_f, dbx_f, llm_f = "#e8f8f5", "#fdecea", "#eaf2f8"
    mute = "#555555"

    ax.text(
        8.25, 10.5,
        "From question to answer — with the Neo4j semantic layer",
        ha="center", fontsize=16, fontweight="bold", color=ink,
    )
    ax.text(
        8.25, 10.18,
        "Same LLM, two tools: hybrid retrieval (Neo4j) then Text2SQL on the Databricks SQL warehouse.",
        ha="center", fontsize=10.5, color=mute,
    )

    # Numbered path (the thing GitHub readers follow)
    steps = [
        (0.35, "1  Question", "#f4f6f7", "#7f8c8d"),
        (3.05, "2  Retrieve", llm_f, llm),
        (5.75, "3  Hybrid Cypher", neo_f, neo),
        (8.55, "4  Text2SQL", llm_f, llm),
        (11.25, "5  execute_sql", dbx_f, dbx),
        (13.95, "6  Answer", "#eafaf1", "#1e8449"),
    ]
    for x, label, face, edge in steps:
        _box(ax, x, 9.42, 2.55, 0.58, face, edge, lw=1.3)
        ax.text(x + 1.275, 9.71, label, ha="center", va="center",
                fontsize=9, fontweight="bold", color=edge)
    for x in (2.9, 5.6, 8.4, 11.1, 13.8):
        ax.annotate("", xy=(x + 0.14, 9.71), xytext=(x - 0.14, 9.71),
                    arrowprops=dict(arrowstyle="-|>", color="#888888", lw=1.3))

    # Three system columns with real gutters
    # Agent 0.30–5.05, Neo4j 5.45–11.15, Databricks 11.55–16.20
    _box(ax, 0.30, 3.95, 4.75, 5.20, llm_f, llm, lw=1.8)
    ax.text(2.68, 8.88, "LLM AGENT", ha="center", fontsize=12.5, fontweight="bold", color=llm)
    ax.text(2.68, 8.60, '1  "What was NRR for EMEA in Dec 2024?"',
            ha="center", fontsize=8.6, color=ink, style="italic")

    _box(ax, 0.48, 7.15, 4.40, 1.28, "white", llm, lw=1.1)
    ax.text(0.66, 8.18, "2  Retriever  (MCP)", fontsize=10, fontweight="bold", color=llm)
    ax.text(0.66, 7.88, "get_context_by_table_hybrid_search", fontsize=8.6, family="monospace", color=ink)
    ax.text(0.66, 7.48, 'text_content = "net revenue retention EMEA"\nmax_tables = 3',
            fontsize=8.2, family="monospace", color=mute, va="top")

    _box(ax, 0.48, 5.55, 4.40, 1.45, "white", llm, lw=1.1)
    ax.text(0.66, 6.72, "4  Text2SQL", fontsize=10, fontweight="bold", color=llm)
    ax.text(0.66, 6.18, "Write Databricks SQL from retrieved\nphysical names: dm_agg_10.a_1007,\nnrr, rgn, mo.",
            fontsize=8.8, color=ink, va="top")

    _box(ax, 0.48, 4.12, 4.40, 1.28, "white", llm, lw=1.1)
    ax.text(0.66, 5.15, "5  Execution tool", fontsize=10, fontweight="bold", color=llm)
    ax.text(0.66, 4.85, "execute_sql", fontsize=8.6, family="monospace", color=ink)
    ax.text(0.66, 4.45, "SELECT nrr FROM ...a_1007\nWHERE rgn = 'EMEA' AND mo = DATE '2024-12-01'",
            fontsize=8.0, family="monospace", color=mute, va="top")

    _box(ax, 5.45, 3.95, 5.70, 5.20, neo_f, neo, lw=1.8)
    ax.text(8.30, 8.88, "NEO4J  SEMANTIC LAYER", ha="center", fontsize=12.5, fontweight="bold", color=neo)
    ax.text(8.30, 8.60, "graph + vector + full-text indexes", ha="center", fontsize=9, color=mute)

    _box(ax, 5.63, 7.25, 2.55, 1.18, "white", neo, lw=1.1)
    ax.text(6.90, 8.18, "VECTOR", ha="center", fontsize=10, fontweight="bold", color=neo)
    ax.text(6.90, 7.72, "db.index.vector.queryNodes\n(table_vector_index)",
            ha="center", fontsize=7.6, family="monospace", color=ink, va="top")

    _box(ax, 8.38, 7.25, 2.55, 1.18, "white", neo, lw=1.1)
    ax.text(9.65, 8.18, "FULL-TEXT", ha="center", fontsize=10, fontweight="bold", color=neo)
    ax.text(9.65, 7.72, "db.index.fulltext.queryNodes\n(table_full_text_index)",
            ha="center", fontsize=7.6, family="monospace", color=ink, va="top")
    ax.text(8.30, 7.84, "union", ha="center", va="center", fontsize=7.5,
            fontweight="bold", color=neo)

    _box(ax, 5.63, 5.85, 5.34, 1.25, "white", neo, lw=1.1)
    ax.text(5.81, 6.82, "3  Hybrid Cypher  (UNION + max score)", fontsize=10, fontweight="bold", color=neo)
    ax.text(5.81, 6.42, "Normalize each branch, keep the stronger score\nper Table, ORDER BY score DESC LIMIT 3.",
            fontsize=8.5, color=ink, va="top")

    _box(ax, 5.63, 4.12, 5.34, 1.58, "white", neo, lw=1.1)
    ax.text(5.81, 5.42, "Graph expansion (same Cypher)", fontsize=10, fontweight="bold", color=neo)
    ax.text(8.30, 4.88, "Database -HAS_SCHEMA-> Schema -HAS_TABLE-> Table\n"
            "Table -HAS_COLUMN-> Column -REFERENCES-> Column\n"
            "Column -HAS_VALUE-> sample values",
            ha="center", fontsize=8.0, family="monospace", color=ink, va="top")
    ax.text(8.30, 4.28, "returns  dm_agg_10.a_1007  + columns + FK paths",
            ha="center", fontsize=8.6, fontweight="bold", color=neo)

    _box(ax, 11.55, 3.95, 4.65, 5.20, dbx_f, dbx, lw=1.8)
    ax.text(13.88, 8.88, "DATABRICKS", ha="center", fontsize=12.5, fontweight="bold", color=dbx)
    ax.text(13.88, 8.60, "Unity Catalog + SQL warehouse", ha="center", fontsize=9, color=mute)

    _box(ax, 11.73, 6.85, 4.30, 1.58, "white", dbx, lw=1.1)
    ax.text(11.91, 8.18, "Unity Catalog  (metadata)", fontsize=10, fontweight="bold", color=dbx)
    ax.text(11.91, 7.72, "264 tables · 5,614 columns\nBusiness meaning lives in COMMENT.\nIngested once via information_schema.",
            fontsize=8.6, color=ink, va="top")

    _box(ax, 11.73, 4.12, 4.30, 2.55, "white", dbx, lw=1.1)
    ax.text(11.91, 6.40, "SQL warehouse  (compute)", fontsize=10, fontweight="bold", color=dbx)
    ax.text(11.91, 6.05, "Runs the generated Databricks SQL.", fontsize=8.8, color=ink)
    ax.text(11.91, 5.45, "SELECT nrr, rgn, mo\nFROM catalog.dm_agg_10.a_1007\nWHERE rgn = 'EMEA'\n  AND mo = DATE '2024-12-01'",
            fontsize=8.0, family="monospace", color=mute, va="top")
    ax.text(11.91, 4.35, "rows  ->  1.08  (108%)", fontsize=10, fontweight="bold", color=dbx)

    # Gutter arrows only (no overlap with box interiors)
    _arrow(ax, 5.05, 7.80, 5.45, 7.80, neo)
    ax.text(5.25, 8.00, "query", ha="center", fontsize=7.5, color=neo)
    _arrow(ax, 5.45, 6.20, 5.05, 6.20, neo)
    ax.text(5.25, 6.38, "context", ha="center", fontsize=7.5, color=neo)
    _arrow(ax, 11.15, 4.75, 11.55, 4.75, dbx)
    ax.text(11.35, 4.93, "SQL", ha="center", fontsize=7.5, color=dbx)
    _arrow(ax, 13.88, 4.12, 13.88, 3.78, "#1e8449")

    _box(ax, 0.30, 3.20, 15.90, 0.58, "#eafaf1", "#1e8449", lw=1.4)
    ax.text(0.50, 3.49, "6  Answer", fontsize=10, fontweight="bold", color="#1e8449", va="center")
    ax.text(8.25, 3.49, "NRR for EMEA in December 2024 was 1.08 (108%).",
            ha="center", va="center", fontsize=12, color=ink)

    _box(ax, 0.30, 0.20, 7.85, 2.80, "#fafafa", "#7f8c8d", lw=1.2)
    ax.text(0.50, 2.70, "ONCE  — build the semantic layer", fontsize=10.5, fontweight="bold", color=ink)
    ax.text(
        0.50, 2.30,
        "DatabricksSchemaConnector reads information_schema\n"
        "(schemas, tables, columns, COMMENT, PK/FK) into Neo4j.\n"
        "Embeddings (OpenAI or local all-mpnet-base-v2) index\n"
        "every Table/Column COMMENT → table_vector_index +\n"
        "table_full_text_index.  Not on the query path.",
        fontsize=8.6, color=ink, va="top",
    )
    ax.text(0.50, 0.40, "Databricks UC  ->  Neo4j   (offline)", fontsize=8.4, style="italic", color=mute)

    _box(ax, 8.35, 0.20, 7.85, 2.80, "#fafafa", "#7f8c8d", lw=1.2)
    ax.text(8.55, 2.70, "MCP tools on the WITH agent", fontsize=10.5, fontweight="bold", color=ink)
    tools = [
        ("get_context_by_table_hybrid_search", "primary — vector + full-text on Table, Cypher expand"),
        ("get_context_by_column_hybrid_search", "optional — same hybrid, anchored on Column"),
        ("get_context_by_schema_and_table_vector_search", "optional — vector-only schema+table lookup"),
        ("execute_sql", "always — Databricks SQL warehouse (Text2SQL execution)"),
    ]
    y = 2.28
    for name, note in tools:
        ax.text(8.55, y, name, fontsize=7.8, family="monospace", color=ink, va="top")
        ax.text(8.55, y - 0.26, note, fontsize=8.0, color=mute, va="top")
        y -= 0.52

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "sl_flow.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def graph_model() -> None:
    """The Neocarta semantic-layer graph: node labels, properties, relationships."""
    fig, ax = plt.subplots(figsize=(15.4, 8.4), dpi=150)
    ax.set_xlim(0, 15.4)
    ax.set_ylim(0, 8.4)
    ax.axis("off")

    ink, mute = "#222222", "#555555"
    db_c, sc_c, tb_c, co_c, va_c = "#1a5276", "#b7950b", "#7b1fa2", "#1e8449", "#c0392b"
    db_f, sc_f, tb_f, co_f, va_f = "#eaf2f8", "#fef9e7", "#f3e5f5", "#eafaf1", "#fdecea"

    cx = 7.7
    ax.text(cx, 8.05, "Neocarta semantic-layer graph — what lives in Neo4j",
            ha="center", fontsize=15, fontweight="bold", color=ink)
    ax.text(cx, 7.68,
            "Only metadata crosses into Neo4j. The rows stay in Databricks.",
            ha="center", fontsize=10, color=mute)

    # Node cards: (x, y, w, h, face, edge, title, props)
    W = 2.55
    nodes = [
        (0.55, 4.35, W, 2.35, db_f, db_c, "Database",
         ["id  · KEY", "name", "description", "embedding : VECTOR"]),
        (4.25, 4.35, W, 2.35, sc_f, sc_c, "Schema",
         ["id  · KEY", "name", "description", "embedding : VECTOR"]),
        (7.95, 4.35, W, 2.35, tb_f, tb_c, "Table",
         ["id  · KEY", "name", "description", "embedding : VECTOR"]),
        (11.65, 3.55, 2.95, 3.15, co_f, co_c, "Column",
         ["id  · KEY", "name", "description", "embedding : VECTOR",
          "type", "isPrimaryKey", "isForeignKey"]),
        (7.95, 0.65, W, 1.75, va_f, va_c, "Value",
         ["id  · KEY", "value"]),
    ]
    for x, y, w, h, face, edge, title, props in nodes:
        _box(ax, x, y, w, h, face, edge, lw=1.9)
        ax.text(x + w / 2, y + h - 0.34, title, ha="center", fontsize=12.5,
                fontweight="bold", color=edge)
        py = y + h - 0.78
        for p in props:
            ax.text(x + 0.22, py, p, fontsize=8.6, family="monospace",
                    color=ink, va="top")
            py -= 0.34

    def edge_label(x, y, text, color):
        ax.text(x, y, text, ha="center", va="center", fontsize=8.4,
                fontweight="bold", color=color,
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=color, lw=1.0))

    # Database -HAS_SCHEMA-> Schema
    _arrow(ax, 3.10, 5.525, 4.25, 5.525, db_c)
    edge_label(3.675, 5.525, "HAS_SCHEMA", db_c)
    # Schema -HAS_TABLE-> Table
    _arrow(ax, 6.80, 5.525, 7.95, 5.525, sc_c)
    edge_label(7.375, 5.525, "HAS_TABLE", sc_c)
    # Table -HAS_COLUMN-> Column
    _arrow(ax, 10.50, 5.525, 11.65, 5.525, tb_c)
    edge_label(11.075, 5.525, "HAS_COLUMN", tb_c)
    # Column -REFERENCES-> Column (self loop, foreign keys)
    ax.annotate("", xy=(13.85, 6.70), xytext=(12.60, 6.70),
                arrowprops=dict(arrowstyle="-|>", color=co_c, lw=1.6,
                                connectionstyle="arc3,rad=-1.4"))
    ax.text(13.225, 7.52, "REFERENCES", ha="center", fontsize=8.4,
            fontweight="bold", color=co_c,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=co_c, lw=1.0))
    ax.text(13.225, 7.14, "(foreign keys → joins)", ha="center", fontsize=7.6,
            color=mute)
    # Column -HAS_VALUE-> Value
    ax.annotate("", xy=(9.70, 2.40), xytext=(12.30, 3.55),
                arrowprops=dict(arrowstyle="-|>", color=va_c, lw=1.6,
                                connectionstyle="arc3,rad=0.25"))
    edge_label(11.35, 2.78, "HAS_VALUE", va_c)

    ax.text(cx, 0.28,
            "Embeddings are generated from each description (COMMENT). "
            "Hybrid search ranks Table/Column, then the agent follows REFERENCES to build joins.",
            ha="center", fontsize=8.8, color=mute, style="italic")

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "graph_model.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def _summarize_sql(sql: str, catalog: str = "") -> tuple[str, str]:
    """Return (short tag, target) for one captured statement."""
    import re

    low = sql.lower()
    if "information_schema.schemata" in low:
        return "scan", "information_schema.schemata  (list all schemas)"
    if "information_schema.tables" in low:
        m = re.search(r"table_schema\s*=\s*'([^']+)'", sql)
        sch = m.group(1) if m else "?"
        return "scan", f"information_schema.tables · {sch}"
    if "information_schema.columns" in low:
        m = re.search(r"table_schema\s*=\s*'([^']+)'\s*AND\s*table_name\s*=\s*'([^']+)'", sql)
        tgt = f"{m.group(1)}.{m.group(2)}" if m else "?"
        return "scan", f"information_schema.columns · {tgt}"
    compact = " ".join(sql.split())
    if catalog:  # drop the long catalog prefix so schema.table stays readable
        compact = compact.replace(f"`{catalog}`.", "").replace(f"{catalog}.", "")
    compact = compact.replace(" = ", "=")
    if len(compact) > 50:
        compact = compact[:47] + "…"
    return "QUERY", compact


def query_history_compare(path: Path | None = None) -> None:
    """Side-by-side Databricks query history for one question: WITHOUT vs WITH."""
    path = path or (DOCS / "query_log.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    wo, wi = data["runs"]["without"], data["runs"]["with"]

    red, redf = "#c0392b", "#fdecea"
    neo, neof = "#1e8449", "#eafaf1"
    ink, mute, grey = "#222222", "#666666", "#8a8a8a"

    fig, ax = plt.subplots(figsize=(16.4, 9.8), dpi=150)
    ax.set_xlim(0, 16.4)
    ax.set_ylim(0, 9.8)
    ax.axis("off")

    ax.text(8.2, 9.48, "One question, two runs — what the Databricks warehouse actually sees",
            ha="center", fontsize=16, fontweight="bold", color=ink)
    ax.text(8.2, 9.14, f'“{data["question"]}”   ·   same model, same final SQL, same answer (108%)',
            ha="center", fontsize=10.5, color=mute, style="italic")

    def panel(x0, w, run, color, facef, title, subtitle, extra_rows=None):
        _box(ax, x0, 0.45, w, 8.30, facef, color, lw=2.0)
        ax.text(x0 + 0.28, 8.42, title, fontsize=13.5, fontweight="bold", color=color)
        ax.text(x0 + 0.28, 8.13, subtitle, fontsize=9.2, color=mute)
        # stats line
        stats = (f'{run["n_warehouse_queries"]} warehouse '
                 f'{"queries" if run["n_warehouse_queries"] != 1 else "query"}   ·   '
                 f'{run["n_tool_calls"]} tool calls   ·   '
                 f'{run["tokens_total"]:,} tokens   ·   ${run["cost"]:.4f}')
        ax.text(x0 + 0.28, 7.80, stats, fontsize=9.4, fontweight="bold", color=color)
        ax.text(x0 + 0.30, 7.47, "#", fontsize=8, fontweight="bold", color=grey)
        ax.text(x0 + 0.72, 7.47, "warehouse round-trip", fontsize=8, fontweight="bold", color=grey)
        ax.text(x0 + w - 0.30, 7.47, "time", fontsize=8, fontweight="bold", color=grey, ha="right")
        _arrow(ax, x0 + 0.30, 7.34, x0 + w - 0.30, 7.34, "#cccccc")
        y, dy = 7.08, 0.345
        for note in extra_rows or []:
            ax.text(x0 + 0.72, y, note, fontsize=8.2, family="monospace", color=neo,
                    fontweight="bold", va="center")
            y -= dy
            ax.text(x0 + 0.72, y, "↳ runs in Neo4j over MCP — never hits the warehouse",
                    fontsize=8.0, color=mute, style="italic", va="center")
            y -= dy + 0.12
        for q in run["warehouse_queries"]:
            tag, target = _summarize_sql(q["sql"], data.get("catalog", ""))
            is_biz = q["kind"] == "business"
            ax.text(x0 + 0.30, y, str(q["n"]), fontsize=7.6, family="monospace", color=grey, va="center")
            ax.text(x0 + 0.72, y, tag, fontsize=7.2, family="monospace", fontweight="bold",
                    color="white", va="center",
                    bbox=dict(boxstyle="round,pad=0.16", fc=(neo if is_biz else "#cd6155"), ec="none"))
            ax.text(x0 + 1.66, y, target, fontsize=8.0, family="monospace",
                    color=(ink if is_biz else "#7b241c"),
                    fontweight=("bold" if is_biz else "normal"), va="center")
            ax.text(x0 + w - 0.30, y, f'{q["ms"]:,}ms', fontsize=7.0, family="monospace",
                    color=grey, va="center", ha="right")
            y -= dy
        return y

    y_left = panel(
        0.35, 7.55, wo, red, redf,
        "WITHOUT the semantic layer",
        "agent brute-forces Unity Catalog to find where “NRR” lives",
    )
    ax.text(0.72, y_left + 0.02,
            f'↑ {wo["n_catalog_queries"]} information_schema scans just to locate one table',
            fontsize=8.6, color=red, style="italic", va="center")

    panel(
        8.50, 7.55, wi, neo, neof,
        "WITH the Neo4j semantic layer",
        "one hybrid retrieval in Neo4j, then the query",
        extra_rows=["retrieve  get_context_by_table_hybrid_search"],
    )

    fig.text(0.5, 0.008,
             "Same question, same final SQL (dm_agg_10.a_1007), same answer — but without the "
             "semantic layer the warehouse fields 18 round-trips to answer one question.",
             ha="center", fontsize=9.5, color=mute, style="italic")

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "query_history_compare.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def semantic_layer_overview() -> None:
    """A semantic layer sits over many source types — not just databases."""
    W = 14.2
    fig, ax = plt.subplots(figsize=(W, 8.4), dpi=150)
    ax.set_xlim(0, W)
    ax.set_ylim(0, 8.4)
    ax.axis("off")

    ink, mute = "#222222", "#555555"
    neo, neof = "#1e8449", "#eafaf1"
    llm, llmf = "#1a5276", "#eaf2f8"
    src, srcf = "#5d6d7e", "#f4f6f7"

    ax.text(W / 2, 8.05, "A semantic layer sits over many sources — not just databases",
            ha="center", fontsize=15.5, fontweight="bold", color=ink)
    ax.text(W / 2, 7.68, "Only metadata is ingested — your data stays in the source.",
            ha="center", fontsize=10.5, color=mute)

    sources = [
        ("Warehouses & Lakes", "BigQuery · Databricks\nSnowflake · Postgres"),
        ("Data Catalogs", "Unity Catalog · Collibra\nAlation · Dataplex"),
        ("Query Logs", "parsed join paths\n& usage patterns"),
        ("Glossaries & Ontologies", "business terms,\nmetric defs, docs"),
    ]
    n = len(sources)
    gap, margin = 0.4, 0.5
    w = (W - 2 * margin - (n - 1) * gap) / n
    y, h = 5.75, 1.35
    centers = []
    for i, (title, sub) in enumerate(sources):
        x = margin + i * (w + gap)
        _box(ax, x, y, w, h, srcf, src, lw=1.5)
        cx = x + w / 2
        centers.append(cx)
        ax.text(cx, y + h - 0.34, title, ha="center", fontsize=10.5, fontweight="bold", color=src)
        ax.text(cx, y + 0.44, sub, ha="center", fontsize=8.4, color=ink, va="center")

    slw, slh = 5.6, 1.55
    slx, sly = (W - slw) / 2, 3.15
    _box(ax, slx, sly, slw, slh, neof, neo, lw=2.2)
    ax.text(W / 2, sly + slh - 0.42, "Semantic Layer — Neo4j", ha="center",
            fontsize=13.5, fontweight="bold", color=neo)
    ax.text(W / 2, sly + 0.52,
            "graph of metadata: tables, columns, joins,\nglossary terms + embeddings",
            ha="center", fontsize=9.2, color=ink, va="center")

    for i, cx in enumerate(centers):
        tx = slx + slw * (i + 0.5) / n
        _arrow(ax, cx, y - 0.03, tx, sly + slh + 0.03, src)
    ax.text(W / 2, y - 0.42, "metadata", ha="center", fontsize=9,
            fontstyle="italic", color=mute,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none"))

    agw, agh = 3.4, 1.15
    agx, agy = (W - agw) / 2, 0.75
    _box(ax, agx, agy, agw, agh, llmf, llm, lw=2.0)
    ax.text(W / 2, agy + agh / 2 + 0.1, "AI Agent", ha="center",
            fontsize=12.5, fontweight="bold", color=llm)
    ax.text(W / 2, agy + agh / 2 - 0.28, "Text2SQL · retrieval over MCP",
            ha="center", fontsize=8.6, color=mute)
    _arrow(ax, W / 2 - 0.5, sly - 0.03, W / 2 - 0.5, agy + agh + 0.03, neo)
    _arrow(ax, W / 2 + 0.5, agy + agh + 0.03, W / 2 + 0.5, sly - 0.03, llm)
    ax.text(W / 2 - 0.72, (sly + agy + agh) / 2, "context", ha="right",
            fontsize=8.4, fontstyle="italic", color=neo)
    ax.text(W / 2 + 0.72, (sly + agy + agh) / 2, "question", ha="left",
            fontsize=8.4, fontstyle="italic", color=llm)

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "semantic_layer_overview.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", action="store_true", help="token + cost charts only")
    parser.add_argument("--cost", action="store_true", help="cost chart only")
    parser.add_argument("--flow", action="store_true", help="question-to-answer flow only")
    parser.add_argument("--graph", action="store_true", help="graph data model only")
    parser.add_argument("--querylog", action="store_true", help="query-history compare only")
    parser.add_argument("--overview", action="store_true", help="semantic-layer sources overview only")
    args = parser.parse_args()
    if args.overview:
        semantic_layer_overview()
        return
    if args.querylog:
        query_history_compare()
        return
    if args.graph:
        graph_model()
        return
    if args.flow:
        sl_flow()
        return
    if args.cost:
        cost_chart()
        return
    if not args.results:
        semantic_layer_overview()
        catalog_map(build_lakehouse())
        sl_flow()
        graph_model()
        if (DOCS / "query_log.json").exists():
            query_history_compare()
    if _eval_results_path().exists():
        results_chart()
        cost_chart()


if __name__ == "__main__":
    main()
