"""Provision a Databricks SQL warehouse (and optional catalog) for this demo.

The Azure *workspace* is not a SQL warehouse. Neocarta ingest + Text2SQL need a
warehouse HTTP path. This script creates a small warehouse named ``neocarta-demo``
and prints the values to put in ``.env``.

Auth (first match wins):
  1. ``DATABRICKS_TOKEN`` (personal access token)
  2. Azure AD device-code login (browser at microsoft.com/devicelogin)

Usage:
    uv run python src/provision_databricks.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env", override=True)

from databricks_auth import databricks_hostname, databricks_token  # noqa: E402

HOST = databricks_hostname()
WAREHOUSE_NAME = os.getenv("DATABRICKS_WAREHOUSE_NAME", "neocarta-demo")
CATALOG = os.getenv("DATABRICKS_CATALOG", "neocarta_demo")


def _token() -> str:
    return databricks_token(interactive=True)


def _client(token: str):
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=f"https://{HOST}", token=token)


def _find_warehouse(w, name: str):
    for info in w.warehouses.list():
        if info.name == name:
            return info
    return None


def _create_warehouse(w):
    existing = _find_warehouse(w, WAREHOUSE_NAME)
    if existing:
        print(f"Warehouse `{WAREHOUSE_NAME}` already exists (id={existing.id}).")
        return existing
    print(f"Creating SQL warehouse `{WAREHOUSE_NAME}` (2X-Small, auto-stop 30m)...")
    from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType

    kwargs = dict(
        name=WAREHOUSE_NAME,
        cluster_size="2X-Small",
        auto_stop_mins=30,
        min_num_clusters=1,
        max_num_clusters=1,
        enable_serverless_compute=True,
        warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
    )
    try:
        return w.warehouses.create_and_wait(**kwargs)
    except Exception as serverless_err:  # noqa: BLE001
        print(f"  serverless PRO failed ({serverless_err}); retrying classic warehouse...")
        kwargs["enable_serverless_compute"] = False
        kwargs["warehouse_type"] = CreateWarehouseRequestWarehouseType.CLASSIC
        return w.warehouses.create_and_wait(**kwargs)


def _ensure_running(w, warehouse):
    state = getattr(getattr(warehouse, "state", None), "value", None) or str(
        getattr(warehouse, "state", "")
    )
    print(f"Warehouse state: {state}")
    if str(state).upper() in {"STOPPED", "STOPPING", "DELETED"}:
        print("Starting warehouse...")
        w.warehouses.start_and_wait(id=warehouse.id)
        warehouse = w.warehouses.get(id=warehouse.id)
    return warehouse


def _ensure_catalog(w, name: str) -> None:
    try:
        from databricks.sdk.service.catalog import CatalogInfo

        for cat in w.catalogs.list():
            if cat.name == name:
                print(f"Catalog `{name}` already exists.")
                return
        print(f"Creating catalog `{name}`...")
        w.catalogs.create(name=name, comment="NeoCarta Databricks Text2SQL benchmark lakehouse.")
        print(f"Created catalog `{name}`.")
    except Exception as exc:  # noqa: BLE001
        print(
            f"Could not create catalog `{name}` ({exc}).\n"
            "Create it in the workspace UI (Catalog explorer) or set "
            "DATABRICKS_CATALOG to a catalog you can write to."
        )


def _write_env_hints(http_path: str) -> None:
    env_path = ROOT / ".env"
    lines = [
        f"DATABRICKS_SERVER_HOSTNAME={HOST}",
        f"DATABRICKS_HTTP_PATH={http_path}",
        f"DATABRICKS_CATALOG={CATALOG}",
    ]
    print("\nAdd these to .env (token stays whatever you already set):\n")
    for line in lines:
        print(f"  {line}")
    if not env_path.exists():
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        example = example.replace(
            "DATABRICKS_SERVER_HOSTNAME=dbc-xxxx.cloud.databricks.com",
            f"DATABRICKS_SERVER_HOSTNAME={HOST}",
        )
        example = example.replace(
            "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc123",
            f"DATABRICKS_HTTP_PATH={http_path}",
        )
        env_path.write_text(example, encoding="utf-8")
        print(f"\nWrote a starter {env_path} from .env.example — still fill DATABRICKS_TOKEN / Aura / OpenAI.")
        return
    text = env_path.read_text(encoding="utf-8")
    replacements = {
        "DATABRICKS_SERVER_HOSTNAME=": f"DATABRICKS_SERVER_HOSTNAME={HOST}",
        "DATABRICKS_HTTP_PATH=": f"DATABRICKS_HTTP_PATH={http_path}",
        "DATABRICKS_CATALOG=": f"DATABRICKS_CATALOG={CATALOG}",
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        key = raw.split("=", 1)[0] + "=" if "=" in raw and not raw.strip().startswith("#") else None
        if key in replacements:
            out.append(replacements[key])
            seen.add(key)
        else:
            out.append(raw)
    for key, line in replacements.items():
        if key not in seen:
            out.append(line)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nUpdated {env_path} host / http_path / catalog (token untouched).")


def main() -> None:
    print(f"Workspace: https://{HOST}")
    token = _token()
    w = _client(token)
    me = w.current_user.me()
    print(f"Signed in as: {me.user_name or me.id}")
    warehouse = _ensure_running(w, _create_warehouse(w))
    http_path = warehouse.odbc_params.path if warehouse.odbc_params else None
    if not http_path and warehouse.id:
        http_path = f"/sql/1.0/warehouses/{warehouse.id}"
    print(f"HTTP path: {http_path}")
    _ensure_catalog(w, CATALOG)
    if http_path:
        _write_env_hints(http_path)
    print("\nNext: uv run python src/seed_lakehouse.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
