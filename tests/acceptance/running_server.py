"""A real, in-process-spawned server for acceptance fixtures that need genuine HTTP and
MCP transport (not `TestClient`'s ASGI-only transport, which the `mcp` SDK's streamable-HTTP
client cannot connect through). Generalizes the `_RunningServer` pattern already proven in
`tests/infrastructure/mcp/test_mcp_acceptance.py` so 09.1's fixtures can reuse it instead of
duplicating the uvicorn-in-a-thread bootstrap.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path

import uvicorn

from personal_graph_os.api.app import create_app


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RunningServer:
    """The real packaged FastAPI app, served over a real loopback socket."""

    def __init__(self, database_path: Path, port: int) -> None:
        self.app = create_app(database_path)
        self.base_url = f"http://127.0.0.1:{port}"
        self.mcp_url = f"{self.base_url}/mcp"
        self.token: str = self.app.state.api_token
        self.workspace_id: str = self.app.state.default_workspace_id
        workspace = self.app.state.workspace_repository.get(self.workspace_id)
        self.default_node_type_id: str = workspace.node_types[0].id
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=lambda: asyncio.run(self._server.serve()))

    def start(self) -> None:
        self._thread.start()
        for _ in range(200):
            if self._server.started:
                return
            threading.Event().wait(0.01)
        raise RuntimeError("uvicorn server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
        self.app.state.connection.close()
