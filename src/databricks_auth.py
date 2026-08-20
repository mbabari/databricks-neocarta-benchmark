"""Databricks auth for Azure workspace: PAT or Azure AD device-code token."""

from __future__ import annotations

import os

AZURE_DATABRICKS_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
_PLACEHOLDERS = {"", "dapi...", "dapi", "YOUR_TOKEN"}


def _pat() -> str | None:
    raw = (os.getenv("DATABRICKS_TOKEN") or "").strip()
    if raw.lower() in {p.lower() for p in _PLACEHOLDERS} or raw.endswith("..."):
        return None
    return raw


def databricks_hostname() -> str:
    host = os.getenv("DATABRICKS_SERVER_HOSTNAME")
    if not host:
        raise SystemExit("Set DATABRICKS_SERVER_HOSTNAME in .env")
    return host.removeprefix("https://").rstrip("/")


def databricks_token(*, interactive: bool = True) -> str:
    """Return a PAT or an Azure AD token scoped to Databricks."""
    pat = _pat()
    if pat:
        return pat
    try:
        from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions
    except ImportError as exc:
        raise SystemExit(
            "Set a real DATABRICKS_TOKEN in .env (not dapi...), or install azure-identity."
        ) from exc
    if not interactive:
        raise SystemExit("No DATABRICKS_TOKEN and interactive Azure login is disabled.")

    def _prompt(verification_uri: str, user_code: str, expires_on) -> None:
        print("No PAT in .env. Azure device-code login for Databricks...", flush=True)
        print(f"Open {verification_uri}", flush=True)
        print(f"Enter code: {user_code}", flush=True)
        print(f"Expires: {expires_on}", flush=True)

    cred = DeviceCodeCredential(
        prompt_callback=_prompt,
        cache_persistence_options=TokenCachePersistenceOptions(
            name="neocarta-databricks",
            allow_unencrypted_storage=True,
        ),
    )
    return cred.get_token(f"{AZURE_DATABRICKS_RESOURCE}/.default").token


def sql_connection(*, http_path: str | None = None):
    from databricks import sql

    host = databricks_hostname()
    path = http_path or os.getenv("DATABRICKS_HTTP_PATH")
    if not path:
        raise SystemExit("Set DATABRICKS_HTTP_PATH (SQL warehouse HTTP path).")
    return sql.connect(
        server_hostname=host,
        http_path=path,
        access_token=databricks_token(),
    )
