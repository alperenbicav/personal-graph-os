"""Run the local API: `uv run python -m personal_graph_os.api`.

Binds to loopback only by default, matching the product contract's "never expose an
unauthenticated write surface to LAN/Tailscale" — remote access is a documented, explicit
opt-in for a later story (ST-08), not this entrypoint's default.
"""

from __future__ import annotations

import uvicorn

from personal_graph_os.api.app import create_app

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    app = create_app()
    print(f"Personal Graph OS API token: {app.state.api_token}")
    print("Add this to frontend/.env.local as VITE_API_TOKEN=<token above> to let the UI connect.")
    uvicorn.run(app, host=LOOPBACK_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
