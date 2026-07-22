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

from personal_graph_os.application.workflow_chain import WorkflowChainStep
from personal_graph_os.domain.identifiers import (
    EdgeTypeId,
    NodeId,
    NodeTypeId,
    ResourceId,
    StatusDefinitionId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.infrastructure.mcp.gateway import (
    MAX_LIST_LIMIT,
    MAX_SEARCH_LIMIT,
    AgentGatewayService,
    GatewayConflictError,
    GatewayNotFoundError,
    GatewayValidationError,
)

SERVER_NAME = "personal-graph-os"


def _limit_property(default: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 1, "maximum": default, "default": default}


# Every mutating tool requires bounded attribution (decision #13, `WORK.md`): a claimed actor
# label, a human-readable reason, and a per-call idempotency key. Merged into each mutating
# tool's own `properties`/`required` rather than duplicated per tool.
_ATTRIBUTION_PROPERTIES: dict[str, object] = {
    "actor_name": {"type": "string", "maxLength": 200},
    "reason": {"type": "string", "maxLength": 1000},
    "request_id": {"type": "string", "maxLength": 200},
}
_ATTRIBUTION_REQUIRED = ("actor_name", "reason", "request_id")


def _mutating_tool(
    name: str,
    description: str,
    *,
    properties: dict[str, object],
    required: tuple[str, ...],
) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {**properties, **_ATTRIBUTION_PROPERTIES},
            "required": [*required, *_ATTRIBUTION_REQUIRED],
            "additionalProperties": False,
        },
    )


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
    _mutating_tool(
        "pgos_create_node",
        "Create a node. Attributed and idempotent by request_id.",
        properties={
            "workspace_id": {"type": "string"},
            "node_type_id": {"type": "string"},
            "title": {"type": "string", "maxLength": 300},
        },
        required=("workspace_id", "node_type_id", "title"),
    ),
    _mutating_tool(
        "pgos_update_node",
        "Update a node's title/body/status/field values. Attributed and idempotent by request_id.",
        properties={
            "node_id": {"type": "string"},
            "title": {"type": "string", "maxLength": 300},
            "body": {"type": "string"},
            "status_id": {"type": "string"},
            "field_values": {"type": "object"},
        },
        required=("node_id",),
    ),
    _mutating_tool(
        "pgos_archive_node",
        "Archive a node. Attributed and idempotent by request_id.",
        properties={"node_id": {"type": "string"}},
        required=("node_id",),
    ),
    _mutating_tool(
        "pgos_connect_nodes",
        "Create a typed edge between two existing nodes. Attributed and idempotent by request_id.",
        properties={
            "workspace_id": {"type": "string"},
            "edge_type_id": {"type": "string"},
            "source_node_id": {"type": "string"},
            "target_node_id": {"type": "string"},
        },
        required=("workspace_id", "edge_type_id", "source_node_id", "target_node_id"),
    ),
    _mutating_tool(
        "pgos_create_or_reuse_resource",
        "Import one resource by canonical identity, reusing an existing match instead of "
        "duplicating it. Attributed and idempotent by request_id; a reuse result never "
        "invents a mutation event.",
        properties={
            "workspace_id": {"type": "string"},
            "title": {"type": "string", "maxLength": 300},
            "raw_source": {"type": "string"},
            "kind": {"type": "string", "enum": [kind.value for kind in ResourceKind]},
            "body": {"type": "string"},
        },
        required=("workspace_id", "title", "raw_source"),
    ),
    _mutating_tool(
        "pgos_update_resource",
        "Update a resource's lifecycle/progress fields. A no-op call (state unchanged) is "
        "reported as such and never invents a mutation event. Attributed and idempotent by "
        "request_id.",
        properties={
            "resource_id": {"type": "string"},
            "lifecycle_status": {
                "type": "string",
                "enum": [status.value for status in ResourceLifecycleStatus],
            },
            "next_action": {"type": "string"},
            "clear_next_action": {"type": "boolean", "default": False},
            "next_action_dismissed": {"type": "boolean"},
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "takeaways": {"type": "array", "items": {"type": "string"}},
            "progress_percent": {"type": "integer", "minimum": 0, "maximum": 100},
            "clear_progress_percent": {"type": "boolean", "default": False},
        },
        required=("resource_id",),
    ),
    _mutating_tool(
        "pgos_archive_resource",
        "Archive a resource. Attributed and idempotent by request_id.",
        properties={"resource_id": {"type": "string"}},
        required=("resource_id",),
    ),
    _mutating_tool(
        "pgos_advance_workflow",
        "Advance the guided Resource -> Takeaway -> Decision -> Task -> Implementation chain "
        "by one step, creating or selecting the target node. Attributed and idempotent by "
        "request_id.",
        properties={
            "workspace_id": {"type": "string"},
            "source_node_id": {"type": "string"},
            "step": {"type": "string", "enum": [step.value for step in WorkflowChainStep]},
            "title": {"type": "string", "maxLength": 300},
            "existing_target_node_id": {"type": "string"},
        },
        required=("workspace_id", "source_node_id", "step"),
    ),
)

_ToolHandler = Callable[[AgentGatewayService, dict[str, object]], dict[str, object]]


def _int_arg(arguments: dict[str, object], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{key} must be an integer")
    return int(value)


def _str_arg(arguments: dict[str, object], key: str) -> str:
    return str(arguments[key])


def _optional_str_arg(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    return None if value is None else str(value)


def _optional_int_arg(arguments: dict[str, object], key: str) -> int | None:
    value = arguments.get(key)
    return None if value is None else _int_arg(arguments, key, 0)


def _optional_bool_arg(arguments: dict[str, object], key: str) -> bool | None:
    value = arguments.get(key)
    return None if value is None else bool(value)


def _optional_str_tuple_arg(arguments: dict[str, object], key: str) -> tuple[str, ...] | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(str(item) for item in value)


def _attribution_args(arguments: dict[str, object]) -> tuple[str, str, str]:
    return (
        _str_arg(arguments, "actor_name"),
        _str_arg(arguments, "reason"),
        _str_arg(arguments, "request_id"),
    )


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


def _create_node(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.create_node(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        NodeTypeId(_str_arg(arguments, "node_type_id")),
        _str_arg(arguments, "title"),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _update_node(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    status_id = _optional_str_arg(arguments, "status_id")
    field_values = arguments.get("field_values")
    if field_values is not None and not isinstance(field_values, dict):
        raise ValueError("field_values must be an object")
    return gateway.update_node(
        NodeId(_str_arg(arguments, "node_id")),
        title=_optional_str_arg(arguments, "title"),
        body=_optional_str_arg(arguments, "body"),
        status_id=StatusDefinitionId(status_id) if status_id is not None else None,
        field_values=field_values,
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _archive_node(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.archive_node(
        NodeId(_str_arg(arguments, "node_id")),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _connect_nodes(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.connect_nodes(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        EdgeTypeId(_str_arg(arguments, "edge_type_id")),
        NodeId(_str_arg(arguments, "source_node_id")),
        NodeId(_str_arg(arguments, "target_node_id")),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _create_or_reuse_resource(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    kind = _optional_str_arg(arguments, "kind")
    return gateway.create_or_reuse_resource(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        _str_arg(arguments, "title"),
        _str_arg(arguments, "raw_source"),
        kind=ResourceKind(kind) if kind is not None else None,
        body=_optional_str_arg(arguments, "body") or "",
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _update_resource(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    lifecycle_status = _optional_str_arg(arguments, "lifecycle_status")
    return gateway.update_resource(
        ResourceId(_str_arg(arguments, "resource_id")),
        lifecycle_status=(
            ResourceLifecycleStatus(lifecycle_status) if lifecycle_status is not None else None
        ),
        next_action=_optional_str_arg(arguments, "next_action"),
        clear_next_action=bool(arguments.get("clear_next_action", False)),
        next_action_dismissed=_optional_bool_arg(arguments, "next_action_dismissed"),
        open_questions=_optional_str_tuple_arg(arguments, "open_questions"),
        takeaways=_optional_str_tuple_arg(arguments, "takeaways"),
        progress_percent=_optional_int_arg(arguments, "progress_percent"),
        clear_progress_percent=bool(arguments.get("clear_progress_percent", False)),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _archive_resource(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.archive_resource(
        ResourceId(_str_arg(arguments, "resource_id")),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _advance_workflow(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    existing_target_node_id = _optional_str_arg(arguments, "existing_target_node_id")
    return gateway.advance_workflow(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        NodeId(_str_arg(arguments, "source_node_id")),
        WorkflowChainStep(_str_arg(arguments, "step")),
        title=_optional_str_arg(arguments, "title"),
        existing_target_node_id=(
            NodeId(existing_target_node_id) if existing_target_node_id is not None else None
        ),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


_HANDLERS: dict[str, _ToolHandler] = {
    "pgos_get_workspace": _get_workspace,
    "pgos_list_nodes": _list_nodes,
    "pgos_get_node": _get_node,
    "pgos_list_edges": _list_edges,
    "pgos_search": _search,
    "pgos_list_resources": _list_resources,
    "pgos_get_resource": _get_resource,
    "pgos_list_node_evidence": _list_node_evidence,
    "pgos_create_node": _create_node,
    "pgos_update_node": _update_node,
    "pgos_archive_node": _archive_node,
    "pgos_connect_nodes": _connect_nodes,
    "pgos_create_or_reuse_resource": _create_or_reuse_resource,
    "pgos_update_resource": _update_resource,
    "pgos_archive_resource": _archive_resource,
    "pgos_advance_workflow": _advance_workflow,
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
        except (GatewayNotFoundError, GatewayValidationError, GatewayConflictError) as error:
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
