"""Real-client protocol acceptance: drives the actual `mcp` SDK client over a real HTTP
connection (not direct handler calls) through initialize/list_tools/call_tool, proving auth
rejection and canonical read behavior end-to-end."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from personal_graph_os.api.app import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _RunningServer:
    def __init__(self, app, port: int) -> None:
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.token: str = app.state.api_token
        self.workspace_id: str = app.state.default_workspace_id
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
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


@pytest.fixture
def running_server(tmp_path: Path) -> Iterator[_RunningServer]:
    app = create_app(tmp_path / "test-workspace.db")
    server = _RunningServer(app, _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


@asynccontextmanager
async def _session(url: str, token: str) -> AsyncGenerator[ClientSession]:
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (
        read,
        write,
        _unused_get_session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _list_tool_names(server: _RunningServer) -> list[str]:
    async with _session(server.url, server.token) as session:
        result = await session.list_tools()
        return [tool.name for tool in result.tools]


def test_real_client_lists_all_read_tools(running_server: _RunningServer) -> None:
    names = asyncio.run(_list_tool_names(running_server))

    assert set(names) == {
        "pgos_get_workspace",
        "pgos_list_nodes",
        "pgos_get_node",
        "pgos_list_edges",
        "pgos_search",
        "pgos_list_resources",
        "pgos_get_resource",
        "pgos_list_node_evidence",
    }


async def _call_get_workspace(server: _RunningServer) -> types.CallToolResult:
    async with _session(server.url, server.token) as session:
        return await session.call_tool("pgos_get_workspace", {"workspace_id": server.workspace_id})


def test_real_client_calls_get_workspace_tool(running_server: _RunningServer) -> None:
    result = asyncio.run(_call_get_workspace(running_server))

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["name"] == "Personal"


async def _call_unknown_workspace(server: _RunningServer) -> types.CallToolResult:
    async with _session(server.url, server.token) as session:
        return await session.call_tool("pgos_get_workspace", {"workspace_id": "does-not-exist"})


def test_real_client_tool_error_is_bounded_not_a_crash(running_server: _RunningServer) -> None:
    result = asyncio.run(_call_unknown_workspace(running_server))

    assert result.isError is True
    error_content = result.content[0]
    assert isinstance(error_content, types.TextContent)
    assert "does-not-exist" in error_content.text


async def _call_over_limit(server: _RunningServer) -> types.CallToolResult:
    async with _session(server.url, server.token) as session:
        return await session.call_tool(
            "pgos_list_nodes", {"workspace_id": server.workspace_id, "limit": 500}
        )


def test_real_client_rejects_limit_above_input_schema_maximum(
    running_server: _RunningServer,
) -> None:
    result = asyncio.run(_call_over_limit(running_server))

    assert result.isError is True


async def _initialize_with_wrong_token(server: _RunningServer) -> None:
    async with streamablehttp_client(
        server.url, headers={"Authorization": "Bearer wrong-token"}
    ) as (read, write, _unused_get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()


def test_real_client_is_rejected_before_initialize_with_wrong_token(
    running_server: _RunningServer,
) -> None:
    with pytest.raises(Exception):  # noqa: B017 - transport surface raises varied client errors
        asyncio.run(_initialize_with_wrong_token(running_server))
