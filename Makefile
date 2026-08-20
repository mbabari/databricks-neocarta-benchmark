.PHONY: sync provision seed dump-sql ingest ingest-meta agent-with agent-without agent-compact runall benchmark

sync:
	uv sync

provision:
	uv run python src/provision_databricks.py

dump-sql:
	uv run python src/seed_lakehouse.py --dump-sql

seed:
	uv run python src/seed_lakehouse.py

ingest:
	uv run python src/build_semantic_layer.py

ingest-meta:
	uv run python src/build_semantic_layer.py --skip-embeddings

agent-with:
	uv run python src/agent.py --mode with

agent-without:
	uv run python src/agent.py --mode without

agent-compact:
	uv run python src/agent.py --mode with --compact

runall:
	uv run python src/agent.py --runall --compact

benchmark:
	uv run python src/benchmark.py
