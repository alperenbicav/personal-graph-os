"""Run the local API: `uv run python -m personal_graph_os.api`.

Binds to loopback only by default and serves the built frontend same-origin under `/app/`
when a production build is present, matching ST-08's "one same-origin process boundary"
contract: no compiled/build-time token, no direct LAN/public bind, and no CORS unless a
developer explicitly opts into it for a separate Vite dev server.
"""

from __future__ import annotations

import os

import uvicorn

from personal_graph_os.api.app import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_STATIC_DIR,
    DEFAULT_TRUSTED_HOSTS,
    DEV_FRONTEND_ORIGINS,
    create_app,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _env_list(name: str) -> list[str] | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or None


def main() -> None:
    trusted_hosts = _env_list("PGOS_TRUSTED_HOSTS") or DEFAULT_TRUSTED_HOSTS
    if os.environ.get("PGOS_DEV_CORS", "").strip().lower() in {"1", "true", "yes"}:
        cors_origins = _env_list("PGOS_DEV_CORS_ORIGINS") or DEV_FRONTEND_ORIGINS
    else:
        cors_origins = None
    static_dir_env = os.environ.get("PGOS_STATIC_DIR")
    if static_dir_env:
        static_dir = static_dir_env
    elif DEFAULT_STATIC_DIR.is_dir():
        static_dir = DEFAULT_STATIC_DIR
    else:
        static_dir = None
    # Isolates a fresh acceptance/staging workspace from the developer's own
    # workspace/graph.db; unset, behavior is identical to before this existed.
    database_path = os.environ.get("PGOS_DB_PATH") or DEFAULT_DATABASE_PATH
    port = int(os.environ.get("PGOS_PORT") or DEFAULT_PORT)

    app = create_app(
        database_path,
        trusted_hosts=trusted_hosts,
        cors_origins=cors_origins,
        static_dir=static_dir,
    )
    print(f"Personal Graph OS API listening on http://{LOOPBACK_HOST}:{port}")
    print("The bearer token is never printed here. Run `pgos-token show` to read it.")
    if static_dir is None:
        print("No production frontend build found; serving API/MCP only (no /app/ UI).")
    uvicorn.run(app, host=LOOPBACK_HOST, port=port)


if __name__ == "__main__":
    main()
