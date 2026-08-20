"""Seed the synthetic Unity Catalog lakehouse via a Databricks SQL warehouse.

Usage:
    uv run python src/seed_lakehouse.py              # apply to the warehouse
    uv run python src/seed_lakehouse.py --dump-sql   # write sql/lakehouse.sql only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse import (  # noqa: E402
    SCHEMA_COMMENTS,
    build_lakehouse,
    lakehouse_stats,
    render_sql,
    render_statements,
)
from databricks_auth import sql_connection  # noqa: E402

load_dotenv(ROOT / ".env", override=True)


def _catalog() -> str:
    catalog = os.getenv("DATABRICKS_CATALOG", "neocarta_demo")
    if not catalog:
        raise SystemExit("Set DATABRICKS_CATALOG.")
    return catalog


def dump_sql(path: Path, catalog: str) -> None:
    tables = build_lakehouse()
    stats = lakehouse_stats(tables)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_sql(catalog, tables), encoding="utf-8")
    print(
        f"Wrote {path} ({stats['tables']} tables, {stats['columns']} columns, "
        f"{stats['fks']} FKs, {stats['seeded_tables']} seeded tables, "
        f"{stats['schemas']} schemas)."
    )


def apply(catalog: str) -> None:
    tables = build_lakehouse()
    stats = lakehouse_stats(tables)
    stmts = render_statements(catalog, tables)
    print(
        f"Applying {len(stmts)} statements to catalog `{catalog}` "
        f"({stats['tables']} tables)..."
    )
    with sql_connection() as conn:
        with conn.cursor() as cur:
            for i, stmt in enumerate(stmts, 1):
                preview = " ".join(stmt.split())[:120]
                try:
                    cur.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    # CREATE CATALOG often needs metastore admin; continue if the
                    # catalog already exists / we only have schema privileges.
                    if i == 1 and "CATALOG" in stmt.upper():
                        print(f"  note: CREATE CATALOG skipped ({exc})")
                        continue
                    raise RuntimeError(f"Failed statement {i}/{len(stmts)}: {preview}\n{exc}") from exc
                if i % 25 == 0 or i == len(stmts):
                    print(f"  {i}/{len(stmts)} ok")

            cur.execute(
                f"""
                SELECT table_schema, COUNT(DISTINCT table_name) AS tables, COUNT(*) AS columns
                FROM `{catalog}`.information_schema.columns
                WHERE table_schema IN ({", ".join(repr(s) for s in SCHEMA_COMMENTS)})
                GROUP BY table_schema
                ORDER BY table_schema
                """
            )
            rows = cur.fetchall()
            print("\ninformation_schema.columns coverage:")
            empty: list[str] = []
            seen: set[str] = set()
            for schema, n_tables, n_cols in rows:
                seen.add(schema)
                print(f"  {schema:<22} tables={n_tables:<4} columns={n_cols}")
                if n_cols == 0:
                    empty.append(schema)
            missing = [s for s in SCHEMA_COMMENTS if s not in seen]
            if missing:
                raise SystemExit(
                    f"Schemas missing from information_schema.columns: {missing}"
                )
            if empty:
                raise SystemExit(
                    "These schemas have tables but ZERO columns in information_schema "
                    f"(Neocarta cannot ingest them): {empty}"
                )
            print("\nSeed verification passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-sql",
        action="store_true",
        help="Write sql/lakehouse.sql and exit (no warehouse connection).",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="Override DATABRICKS_CATALOG.",
    )
    args = parser.parse_args()
    catalog = args.catalog or _catalog()
    sql_path = ROOT / "sql" / "lakehouse.sql"
    dump_sql(sql_path, catalog)
    if args.dump_sql:
        return
    apply(catalog)


if __name__ == "__main__":
    main()
