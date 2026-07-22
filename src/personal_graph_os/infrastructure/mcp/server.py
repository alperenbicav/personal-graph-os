"""The MCP SDK adapter: tool schemas and dispatch live only here.

`AgentGatewayService` is the only application-layer dependency; this module translates its
typed results to/from the wire-level `mcp` SDK objects. No REST handler, application
service, or domain module may import from here or from `mcp` — the dependency points one
way, adapter -> application, per the epic's locked decision that SDK behavior stays
adapter-local.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.types import Receive, Scope, Send

from personal_graph_os.domain.identifiers import NodeId, ResourceId, WorkspaceId
from personal_graph_os.infrastructure.mcp.gateway import (
    MAX_LIST_LIMIT,
    MAX_SEARCH_LIMIT,
    AgentGatewayService,
    GatewayNotFoundError,
    GatewayValidationError,
)

SERVER_NAME = "personal-graph-os"


def _limit_property(default: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 1, "maximum": default, "default": default}


_TOOLS = (
    Tool(
        name="pgos_get_workspace",
        description="Get the workspace's identity and schema summary.",
        inputSchema={
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_list_nodes",
        description="List a workspace's canonical nodes, newest first, bounded and archived-aware.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "include_archived": {"type": "boolean", "default": False},
                "limit": _limit_property(MAX_LIST_LIMIT),
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_get_node",
        description="Get one node by id.",
        inputSchema={
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_list_edges",
        description="List a workspace's canonical edges, bounded and deterministically ordered.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "limit": _limit_property(MAX_LIST_LIMIT),
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_search",
        description="Full-text search over a workspace's nodes and resources.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "query_text": {"type": "string"},
                "include_archived": {"type": "boolean", "default": False},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "default": MAX_SEARCH_LIMIT,
                },
            },
            "required": ["workspace_id", "query_text"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_list_resources",
        description="List a workspace's research resources, most recently active first, bounded.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "limit": _limit_property(MAX_LIST_LIMIT),
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_get_resource",
        description="Get one research resource by id.",
        inputSchema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_list_node_evidence",
        description=(
            "List a node's evidence pointers (attachment/file-reference), as privacy-safe "
            "metadata only — never storage/absolute paths or file bytes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "limit": _limit_property(MAX_LIST_LIMIT),
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
    ),
)

_ToolHandler = Callable[[AgentGatewayService, dict[str, object]], dict[str, object]]


def _int_arg(arguments: dict[str, object], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{key} must be an integer")
    return int(value)


def _get_workspace(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    workspace = gateway.get_workspace(WorkspaceId(str(arguments["workspace_id"])))
    return workspace.model_dump(mode="json")


def _list_nodes(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    nodes = gateway.list_nodes(
        WorkspaceId(str(arguments["workspace_id"])),
        include_archived=bool(arguments.get("include_archived", False)),
        limit=_int_arg(arguments, "limit", MAX_LIST_LIMIT),
    )
    return {"nodes": [node.model_dump(mode="json") for node in nodes]}


def _get_node(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    node = gateway.get_node(NodeId(str(arguments["node_id"])))
    return node.model_dump(mode="json")


def _list_edges(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    edges = gateway.list_edges(
        WorkspaceId(str(arguments["workspace_id"])),
        limit=_int_arg(arguments, "limit", MAX_LIST_LIMIT),
    )
    return {"edges": [edge.model_dump(mode="json") for edge in edges]}


def _search(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    hits = gateway.search(
        WorkspaceId(str(arguments["workspace_id"])),
        str(arguments["query_text"]),
        include_archived=bool(arguments.get("include_archived", False)),
        limit=_int_arg(arguments, "limit", MAX_SEARCH_LIMIT),
    )
    return {"hits": [hit.model_dump(mode="json") for hit in hits]}


def _list_resources(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    resources = gateway.list_resources(
        WorkspaceId(str(arguments["workspace_id"])),
        limit=_int_arg(arguments, "limit", MAX_LIST_LIMIT),
    )
    return {"resources": [resource.model_dump(mode="json") for resource in resources]}


def _get_resource(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    resource = gateway.get_resource(ResourceId(str(arguments["resource_id"])))
    return resource.model_dump(mode="json")


def _list_node_evidence(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    pointers = gateway.list_node_evidence(
        NodeId(str(arguments["node_id"])),
        limit=_int_arg(arguments, "limit", MAX_LIST_LIMIT),
    )
    return {"evidence": [pointer.model_dump(mode="json") for pointer in pointers]}


_HANDLERS: dict[str, _ToolHandler] = {
    "pgos_get_workspace": _get_workspace,
    "pgos_list_nodes": _list_nodes,
    "pgos_get_node": _get_node,
    "pgos_list_edges": _list_edges,
    "pgos_search": _search,
    "pgos_list_resources": _list_resources,
    "pgos_get_resource": _get_resource,
    "pgos_list_node_evidence": _list_node_evidence,
}


def build_mcp_server(gateway: AgentGatewayService) -> Server:
    """Build the low-level MCP `Server`, wired to `gateway` and nothing else."""
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return list(_TOOLS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        handler = _HANDLERS.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        try:
            return handler(gateway, arguments)
        except (GatewayNotFoundError, GatewayValidationError) as error:
            raise ValueError(str(error)) from error

    return server


class _StreamableHTTPASGIApp:
    """Adapts `StreamableHTTPSessionManager.handle_request` to a plain ASGI callable."""

    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._session_manager.handle_request(scope, receive, send)


def create_mcp_asgi_app(
    gateway: AgentGatewayService,
) -> tuple[Callable[[Scope, Receive, Send], Awaitable[None]], StreamableHTTPSessionManager]:
    """Return the raw (unauthenticated) MCP ASGI app plus its session manager.

    The caller (`api/app.py`) is responsible for wrapping the returned app with the bearer
    auth middleware and running the session manager's lifespan for the process lifetime;
    this module has no FastAPI/auth knowledge.
    """
    server = build_mcp_server(gateway)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)
    return _StreamableHTTPASGIApp(session_manager), session_manager


__all__ = ["build_mcp_server", "create_mcp_asgi_app", "TextContent", "json"]
