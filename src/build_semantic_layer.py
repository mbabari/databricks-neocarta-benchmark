"""Ingest Unity Catalog schema metadata into Neo4j via Neocarta.

Uses DatabricksSchemaConnector (SQL warehouse, no Spark) then optional
LiteLLM embeddings for Database / Schema / Table / Column nodes.

Usage:
    uv run python src/build_semantic_layer.py
    uv run python src/build_semantic_layer.py --skip-embeddings
    uv run python src/build_semantic_layer.py --no-value-sampling
    uv run python src/build_semantic_layer.py --only embeddings
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouse import SCHEMA_COMMENTS  # noqa: E402

load_dotenv(ROOT / ".env", override=True)


def _require(keys: list[str]) -> None:
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


def _neo4j_driver():
    _require(["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"])
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )


def _databricks_connection():
    from databricks_auth import sql_connection

    return sql_connection()


def ingest_schema(neo4j_driver, database_name: str, value_sample_limit: int) -> None:
    from neocarta.connectors.databricks import DatabricksSchemaConnector

    catalog = os.getenv("DATABRICKS_CATALOG")
    schemas = list(SCHEMA_COMMENTS)
    print(f"Ingesting {len(schemas)} schemas from catalog `{catalog}` into Neo4j `{database_name}`...")
    with _databricks_connection() as connection:
        for i, schema in enumerate(schemas, 1):
            print(f"  [{i}/{len(schemas)}] {catalog}.{schema}")
            DatabricksSchemaConnector(
                connection=connection,
                catalog=catalog,
                neo4j_driver=neo4j_driver,
                database_name=database_name,
                value_sample_limit=value_sample_limit,
            ).ingest(schema=schema)


def run_embeddings(neo4j_driver, database_name: str) -> None:
    from neocarta import NodeLabel
    from neocarta.enrichment.embeddings import LiteLLMEmbeddingsConnector

    if not os.getenv("OPENAI_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY (or another LiteLLM provider key) for embeddings.")
    print("Generating embeddings for Database / Schema / Table / Column ...")
    kwargs = {}
    if os.getenv("EMBEDDING_DIMENSIONS"):
        kwargs["dimensions"] = int(os.getenv("EMBEDDING_DIMENSIONS"))
    if os.getenv("EMBEDDING_API_BASE"):
        # Custom endpoint (e.g. src/local_embeddings.py) — embeddings only.
        kwargs["litellm_kwargs"] = {"api_base": os.getenv("EMBEDDING_API_BASE")}
    embeddings = LiteLLMEmbeddingsConnector(
        neo4j_driver=neo4j_driver,
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        database_name=database_name,
        **kwargs,
    )
    embeddings.run(
        node_labels=[
            NodeLabel.DATABASE,
            NodeLabel.SCHEMA,
            NodeLabel.TABLE,
            NodeLabel.COLUMN,
        ]
    )


def print_graph_counts(neo4j_driver, database_name: str) -> None:
    query = """
    MATCH (n)
    WITH labels(n)[0] AS label, count(*) AS n
    RETURN label, n
    ORDER BY label
    """
    print("\nNeo4j node counts:")
    with neo4j_driver.session(database=database_name) as session:
        for rec in session.run(query):
            print(f"  {rec['label']:<16} {rec['n']}")
        rels = session.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY t"
        )
        print("Neo4j relationship counts:")
        for rec in rels:
            print(f"  {rec['t']:<16} {rec['n']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Load metadata only (no vector indexes / embeddings).",
    )
    parser.add_argument(
        "--no-value-sampling",
        action="store_true",
        help="Skip sample-value reads (no :Value nodes).",
    )
    parser.add_argument(
        "--only",
        choices=["schema", "embeddings"],
        default=None,
        help="Run a single stage (default: schema then embeddings).",
    )
    args = parser.parse_args()

    database_name = os.getenv("NEO4J_DATABASE", "neo4j")
    value_sample_limit = 0 if args.no_value_sampling else int(os.getenv("VALUE_SAMPLE_LIMIT", "8"))
    do_schema = args.only in {None, "schema"}
    do_embeddings = args.only in {None, "embeddings"} and not args.skip_embeddings
    if args.only == "embeddings":
        do_schema = False
        do_embeddings = True

    driver = _neo4j_driver()
    try:
        if do_schema:
            ingest_schema(driver, database_name, value_sample_limit)
        if do_embeddings:
            run_embeddings(driver, database_name)
        print_graph_counts(driver, database_name)
    finally:
        driver.close()
    print("Semantic layer build completed.")


if __name__ == "__main__":
    main()
