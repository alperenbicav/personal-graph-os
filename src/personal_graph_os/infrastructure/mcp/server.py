"""The MCP SDK adapter: tool schemas and dispatch live only here.

`AgentGatewayService` is the only application-layer dependency; this module translates its
typed results to/from the wire-level `mcp` SDK objects. No REST handler, application
service, or domain module may import from here or from `mcp` — the dependency points one
way, adapter -> application, per the epic's locked decision that SDK behavior stays
adapter-local.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

import anyio
import anyio.to_thread
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import ResourceTemplate, TextContent, Tool
from pydantic import AnyUrl
from starlette.types import Receive, Scope, Send

from personal_graph_os.application.discovery import DiscoveryCandidateInput
from personal_graph_os.application.workflow_chain import WorkflowChainStep
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.identifiers import (
    ActivityEventId,
    CollectionId,
    ContextPackId,
    DocumentId,
    EdgeId,
    EdgeTypeId,
    NodeId,
    NodeTypeId,
    ResourceId,
    StatusDefinitionId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.infrastructure.mcp.gateway import (
    MAX_CONTEXT_PACK_OBJECTS,
    MAX_CONTEXT_PACK_TOKEN_LIMIT,
    MAX_EVIDENCE_POINTER_LENGTH,
    MAX_EVIDENCE_POINTERS,
    MAX_IMPORT_CANDIDATES,
    MAX_IMPORT_EVIDENCE_POINTERS,
    MAX_IMPORT_TEXT_LENGTH,
    MAX_LIST_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SERIALIZED_FIELD_BYTES,
    MAX_SOURCES_SEARCHED,
    AgentGatewayService,
    GatewayConflictError,
    GatewayNotFoundError,
    GatewayValidationError,
)
from personal_graph_os.infrastructure.mcp.telemetry import OperationCounters, record_operation

_CONTEXT_PACK_URI_PREFIX = "pgos://context-packs/"

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
    Tool(
        name="pgos_list_activity_events",
        description=(
            "List a workspace's append-only activity/audit events, most recent first, "
            "bounded. Read-only; each row omits before/after snapshots -- use "
            "pgos_get_activity_event for detail."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "limit": _limit_property(MAX_LIST_LIMIT),
                "cursor": {"type": "string"},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_get_activity_event",
        description="Get one activity/audit event by id, including its bounded before/after "
        "snapshot when recorded.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "event_id": {"type": "string"},
            },
            "required": ["workspace_id", "event_id"],
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
            "body": {"type": "string", "maxLength": MAX_SERIALIZED_FIELD_BYTES},
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
            "raw_source": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
            "kind": {"type": "string", "enum": [kind.value for kind in ResourceKind]},
            "body": {"type": "string", "maxLength": MAX_SERIALIZED_FIELD_BYTES},
        },
        required=("workspace_id", "title", "raw_source"),
    ),
    _mutating_tool(
        "pgos_upsert_document",
        "Create a new Wiki document, or append a new version to the one given by "
        "document_id (or the workspace's existing document with an exact matching title). "
        "Attributed and idempotent by request_id.",
        properties={
            "workspace_id": {"type": "string"},
            "title": {"type": "string", "maxLength": 300},
            "body_markdown": {"type": "string", "maxLength": MAX_SERIALIZED_FIELD_BYTES},
            "kind": {"type": "string", "enum": [kind.value for kind in DocumentKind]},
            "document_id": {"type": "string"},
            "collection_id": {"type": "string"},
            "tag_names": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "maxItems": 20,
            },
        },
        required=("workspace_id", "title", "body_markdown"),
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
            "next_action": {"type": "string", "maxLength": 300},
            "clear_next_action": {"type": "boolean", "default": False},
            "next_action_dismissed": {"type": "boolean"},
            "open_questions": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
                "maxItems": 50,
            },
            "takeaways": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
                "maxItems": 50,
            },
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

_DISCOVERY_CANDIDATE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "identifier": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
        "title": {"type": "string", "maxLength": 300},
        "kind": {"type": "string", "enum": [kind.value for kind in ResourceKind]},
        "description": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
        "evidence": {
            "type": "array",
            "items": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
            "maxItems": MAX_IMPORT_EVIDENCE_POINTERS,
        },
    },
    "required": ["identifier", "title"],
    "additionalProperties": False,
}

_DISCOVERY_IMPORT_PROPERTIES: dict[str, object] = {
    "workspace_id": {"type": "string"},
    "instruction": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
    "sources_searched": {
        "type": "array",
        "items": {"type": "string", "maxLength": MAX_IMPORT_TEXT_LENGTH},
        "maxItems": MAX_SOURCES_SEARCHED,
    },
    "filters_interpreted": {"type": "object"},
    "candidates": {
        "type": "array",
        "items": _DISCOVERY_CANDIDATE_SCHEMA,
        "minItems": 1,
        "maxItems": MAX_IMPORT_CANDIDATES,
    },
}

_DISCOVERY_TOOLS = (
    Tool(
        name="pgos_preview_import",
        description=(
            "Side-effect-free preview of caller-supplied import candidates: canonicalizes "
            "each identifier and reports create/reuse/reject with a reason. Never searches "
            "the web or writes anything."
        ),
        inputSchema={
            "type": "object",
            "properties": _DISCOVERY_IMPORT_PROPERTIES,
            "required": ["workspace_id", "instruction", "candidates"],
            "additionalProperties": False,
        },
    ),
    _mutating_tool(
        "pgos_apply_import",
        "Import caller-supplied candidates into one completed, attributed DiscoveryRun. "
        "Attributed and idempotent by request_id; the actor becomes the run's sole "
        "agent_identity. Never searches the web, fetches a URL, or uses Git/LLM work.",
        properties=_DISCOVERY_IMPORT_PROPERTIES,
        required=("workspace_id", "instruction", "candidates"),
    ),
)

_TOOLS = _TOOLS + _DISCOVERY_TOOLS

_CONTEXT_PACK_SELECTION_PROPERTIES: dict[str, object] = {
    "workspace_id": {"type": "string"},
    "name": {"type": "string", "maxLength": 300},
    "node_ids": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": MAX_CONTEXT_PACK_OBJECTS,
    },
    "edge_ids": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": MAX_CONTEXT_PACK_OBJECTS,
    },
    "evidence_pointers": {
        "type": "array",
        "items": {"type": "string", "maxLength": MAX_EVIDENCE_POINTER_LENGTH},
        "maxItems": MAX_EVIDENCE_POINTERS,
    },
    "inclusion_reasons": {
        "type": "object",
        "description": "Maps every selected node_id/edge_id/evidence_pointer to its reason.",
    },
    "object_limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_CONTEXT_PACK_OBJECTS,
        "default": MAX_CONTEXT_PACK_OBJECTS,
    },
    "token_limit": {"type": "integer", "minimum": 1, "maximum": MAX_CONTEXT_PACK_TOKEN_LIMIT},
}

_CONTEXT_PACK_TOOLS = (
    _mutating_tool(
        "pgos_create_context_pack",
        "Create one immutable, bounded Context Pack manifest selecting existing nodes/edges/"
        "evidence pointers. Every selected object needs an inclusion reason. Attributed and "
        "idempotent by request_id; a Context Pack is never updated, only created or deleted.",
        properties=_CONTEXT_PACK_SELECTION_PROPERTIES,
        required=("workspace_id", "name", "inclusion_reasons"),
    ),
    Tool(
        name="pgos_list_context_packs",
        description="List a workspace's Context Pack manifests.",
        inputSchema={
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_get_context_pack",
        description=(
            "Get one Context Pack's selection manifest by id (not its materialized content)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"context_pack_id": {"type": "string"}},
            "required": ["context_pack_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="pgos_materialize_context_pack",
        description=(
            "Resolve a Context Pack's manifest against current canonical state: returns the "
            "live nodes/edges/resources/evidence, reports missing/archived/unresolved members, "
            "and applies the pack's estimated-token budget with deterministic omissions. Never "
            "snapshots content or dereferences file bytes."
        ),
        inputSchema={
            "type": "object",
            "properties": {"context_pack_id": {"type": "string"}},
            "required": ["context_pack_id"],
            "additionalProperties": False,
        },
    ),
    _mutating_tool(
        "pgos_delete_context_pack",
        "Delete a Context Pack manifest. Attributed and idempotent by request_id.",
        properties={
            "workspace_id": {"type": "string"},
            "context_pack_id": {"type": "string"},
        },
        required=("workspace_id", "context_pack_id"),
    ),
)

_TOOLS = _TOOLS + _CONTEXT_PACK_TOOLS

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


def _list_activity_events(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    page = gateway.list_activity_events(
        WorkspaceId(str(arguments["workspace_id"])),
        limit=_int_arg(arguments, "limit", MAX_LIST_LIMIT),
        cursor=_optional_str_arg(arguments, "cursor"),
    )
    return page.model_dump(mode="json")


def _get_activity_event(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    event = gateway.get_activity_event(
        WorkspaceId(str(arguments["workspace_id"])),
        ActivityEventId(str(arguments["event_id"])),
    )
    return event.model_dump(mode="json")


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


def _upsert_document(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    kind = _optional_str_arg(arguments, "kind")
    document_id = _optional_str_arg(arguments, "document_id")
    collection_id = _optional_str_arg(arguments, "collection_id")
    return gateway.upsert_document(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        _str_arg(arguments, "title"),
        _str_arg(arguments, "body_markdown"),
        kind=DocumentKind(kind) if kind is not None else DocumentKind.NOTE,
        document_id=DocumentId(document_id) if document_id is not None else None,
        collection_id=CollectionId(collection_id) if collection_id is not None else None,
        tag_names=_optional_str_tuple_arg(arguments, "tag_names") or (),
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


def _str_tuple_arg(arguments: dict[str, object], key: str) -> tuple[str, ...]:
    value = arguments.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(str(item) for item in value)


def _dict_arg(arguments: dict[str, object], key: str) -> dict[str, object]:
    value = arguments.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _candidate_inputs(arguments: dict[str, object]) -> list[DiscoveryCandidateInput]:
    raw_candidates = arguments.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be an array")
    candidates: list[DiscoveryCandidateInput] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            raise ValueError("each candidate must be an object")
        kind = raw_candidate.get("kind")
        evidence = raw_candidate.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("candidate evidence must be an array of strings")
        candidates.append(
            DiscoveryCandidateInput(
                identifier=str(raw_candidate["identifier"]),
                title=str(raw_candidate["title"]),
                kind=ResourceKind(str(kind)) if kind is not None else None,
                description=str(raw_candidate.get("description", "")),
                evidence=tuple(str(item) for item in evidence),
            )
        )
    return candidates


def _preview_import(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    preview = gateway.preview_import(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        _str_arg(arguments, "instruction"),
        _candidate_inputs(arguments),
        sources_searched=_str_tuple_arg(arguments, "sources_searched"),
        filters_interpreted=_dict_arg(arguments, "filters_interpreted"),
    )
    return preview.model_dump(mode="json")


def _apply_import(gateway: AgentGatewayService, arguments: dict[str, object]) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.apply_import(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        _str_arg(arguments, "instruction"),
        _candidate_inputs(arguments),
        sources_searched=_str_tuple_arg(arguments, "sources_searched"),
        filters_interpreted=_dict_arg(arguments, "filters_interpreted"),
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _str_str_dict_arg(arguments: dict[str, object], key: str) -> dict[str, str]:
    raw = _dict_arg(arguments, key)
    return {str(k): str(v) for k, v in raw.items()}


def _create_context_pack(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    object_limit = _int_arg(arguments, "object_limit", MAX_CONTEXT_PACK_OBJECTS)
    token_limit = _optional_int_arg(arguments, "token_limit")
    return gateway.create_context_pack(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        _str_arg(arguments, "name"),
        node_ids=tuple(NodeId(item) for item in _str_tuple_arg(arguments, "node_ids")),
        edge_ids=tuple(EdgeId(item) for item in _str_tuple_arg(arguments, "edge_ids")),
        evidence_pointers=_str_tuple_arg(arguments, "evidence_pointers"),
        inclusion_reasons=_str_str_dict_arg(arguments, "inclusion_reasons"),
        object_limit=object_limit,
        token_limit=token_limit,
        actor_name=actor_name,
        reason=reason,
        request_id=request_id,
    )


def _list_context_packs(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    context_packs = gateway.list_context_packs(WorkspaceId(_str_arg(arguments, "workspace_id")))
    return {"context_packs": [pack.model_dump(mode="json") for pack in context_packs]}


def _get_context_pack(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    context_pack = gateway.get_context_pack(ContextPackId(_str_arg(arguments, "context_pack_id")))
    return context_pack.model_dump(mode="json")


def _materialize_context_pack(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    materialization = gateway.materialize_context_pack(
        ContextPackId(_str_arg(arguments, "context_pack_id"))
    )
    return materialization.model_dump(mode="json")


def _delete_context_pack(
    gateway: AgentGatewayService, arguments: dict[str, object]
) -> dict[str, object]:
    actor_name, reason, request_id = _attribution_args(arguments)
    return gateway.delete_context_pack(
        WorkspaceId(_str_arg(arguments, "workspace_id")),
        ContextPackId(_str_arg(arguments, "context_pack_id")),
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
    "pgos_list_activity_events": _list_activity_events,
    "pgos_get_activity_event": _get_activity_event,
    "pgos_create_node": _create_node,
    "pgos_update_node": _update_node,
    "pgos_archive_node": _archive_node,
    "pgos_connect_nodes": _connect_nodes,
    "pgos_create_or_reuse_resource": _create_or_reuse_resource,
    "pgos_upsert_document": _upsert_document,
    "pgos_update_resource": _update_resource,
    "pgos_archive_resource": _archive_resource,
    "pgos_advance_workflow": _advance_workflow,
    "pgos_preview_import": _preview_import,
    "pgos_apply_import": _apply_import,
    "pgos_create_context_pack": _create_context_pack,
    "pgos_list_context_packs": _list_context_packs,
    "pgos_get_context_pack": _get_context_pack,
    "pgos_materialize_context_pack": _materialize_context_pack,
    "pgos_delete_context_pack": _delete_context_pack,
}


_T = TypeVar("_T")


def _drain_stale_interrupt(connection: sqlite3.Connection) -> None:
    """Absorb a `connection.interrupt()` flag that armed after the operation it targeted had
    already finished (a benign race in `_run_gateway_call`): SQLite only raises on the next
    statement that actually observes the flag, so without this a stale flag could otherwise
    abort a completely unrelated later request with a spurious `OperationalError`."""
    try:
        connection.execute("SELECT 1")
    except sqlite3.OperationalError:
        pass


async def _run_gateway_call(
    lock: anyio.Lock,
    connection: sqlite3.Connection | None,
    fn: Callable[[], _T],
) -> _T:
    """Run one synchronous gateway call under `lock`, interruptible by client cancellation
    (ST06-F06).

    `fn` runs on a worker thread so the awaiting MCP task can actually receive
    `CancelledError` while it is still executing (a bare synchronous call on the event loop
    cannot be interrupted mid-mutation). If cancelled, `connection.interrupt()` forces
    SQLite's in-flight statement to abort with `OperationalError`, which unwinds the open
    `ResearchUnitOfWork` transaction (rollback -- no partial Node/Resource/Event/Receipt state)
    before this function re-raises the cancellation. `connection.interrupt()` only affects a
    statement actually executing inside SQLite at that instant; it does nothing while `fn` is
    running ordinary Python between statements, so the worker can still be alive well after
    the interrupt call returns.

    `lock` is therefore held for the worker's *entire* lifetime unconditionally -- there is no
    timeout on waiting for `done`, because releasing the lock (or letting anything else touch
    `connection`) before the worker has actually finished would let an abandoned worker commit
    or otherwise mutate through the shared connection concurrently with a later request
    (ST06-F06: a synchronized probe proved a 5-second bound let exactly this happen). A worker
    thread that never returns leaks a thread and keeps the lock held rather than silently
    permitting that unsafe concurrent use -- an intentional bounded-safety trade-off, since
    `fn` always eventually returns or raises under every real gateway/database call. The lock
    is always released via `async with`, so a cancelled call never leaves the connection or
    the lock in an inconsistent state once the worker has genuinely finished, and the next
    REST/MCP request then proceeds normally. `connection=None` (e.g. a bare unit test server
    with no real database) skips this machinery and runs `fn` directly under `lock`.
    """
    if connection is None:
        async with lock:
            return fn()

    done = threading.Event()

    def guarded() -> _T:
        try:
            return fn()
        finally:
            done.set()

    async with lock:
        try:
            return await anyio.to_thread.run_sync(guarded, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            connection.interrupt()
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(done.wait, abandon_on_cancel=True)
                await anyio.to_thread.run_sync(
                    _drain_stale_interrupt, connection, abandon_on_cancel=True
                )
            raise


def build_mcp_server(
    gateway: AgentGatewayService,
    telemetry: OperationCounters | None = None,
    *,
    db_lock: anyio.Lock | None = None,
    connection: sqlite3.Connection | None = None,
) -> Server:
    """Build the low-level MCP `Server`, wired to `gateway` and nothing else.

    `db_lock` (ST06-F05) is the same shared-connection execution lock REST endpoints use,
    acquired only around the one line that actually touches the gateway/database inside
    `call_tool`/`read_resource` -- never around protocol negotiation, session lifecycle, or an
    idle stream, so REST and MCP serialize only their database work, not each other's
    non-DB time. `None` (e.g. a bare unit test server) means unlocked, single-caller use.
    `connection` (ST06-F06) enables real cancellation of an in-progress mutation; see
    `_run_gateway_call`.
    """
    server: Server = Server(SERVER_NAME)
    counters = telemetry if telemetry is not None else OperationCounters()
    lock = db_lock if db_lock is not None else anyio.Lock()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return list(_TOOLS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        async def run() -> dict[str, object]:
            handler = _HANDLERS.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool: {name}")
            try:
                return await _run_gateway_call(
                    lock, connection, lambda: handler(gateway, arguments)
                )
            except (GatewayNotFoundError, GatewayValidationError, GatewayConflictError) as error:
                raise ValueError(str(error)) from error

        return await record_operation(counters, "tool", name, run)

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                name="context-pack",
                uriTemplate=f"{_CONTEXT_PACK_URI_PREFIX}{{id}}",
                description=(
                    "A Context Pack's materialized content: current nodes/edges/resources/"
                    "evidence plus missing/archived/unresolved/omitted reporting."
                ),
                mimeType="application/json",
            )
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        async def run() -> list[ReadResourceContents]:
            uri_text = str(uri)
            if not uri_text.startswith(_CONTEXT_PACK_URI_PREFIX):
                raise ValueError(f"Unknown resource: {uri_text}")
            context_pack_id = ContextPackId(uri_text[len(_CONTEXT_PACK_URI_PREFIX) :])
            try:
                materialization = await _run_gateway_call(
                    lock, connection, lambda: gateway.materialize_context_pack(context_pack_id)
                )
            except GatewayNotFoundError as error:
                raise ValueError(str(error)) from error
            return [
                ReadResourceContents(
                    content=json.dumps(materialization.model_dump(mode="json")),
                    mime_type="application/json",
                )
            ]

        return await record_operation(counters, "resource", "context-pack", run)

    return server


class _StreamableHTTPASGIApp:
    """Adapts `StreamableHTTPSessionManager.handle_request` to a plain ASGI callable."""

    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._session_manager.handle_request(scope, receive, send)


def create_mcp_asgi_app(
    gateway: AgentGatewayService,
    *,
    db_lock: anyio.Lock | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[
    Callable[[Scope, Receive, Send], Awaitable[None]],
    StreamableHTTPSessionManager,
    OperationCounters,
]:
    """Return the raw (unauthenticated) MCP ASGI app, its session manager, and its telemetry
    counters (06.5, `WORK.md`) — operation/tool/result/duration/count only, never arguments,
    content, actor, paths, or raw exceptions.

    The caller (`api/app.py`) is responsible for wrapping the returned app with the bearer
    auth middleware and running the session manager's lifespan for the process lifetime;
    this module has no FastAPI/auth knowledge. `db_lock` (ST06-F05) should be the same lock
    REST uses, so REST and MCP database work never interleaves unsafely on the shared
    connection, without the MCP session itself sitting inside that lock while idle.
    `connection` (ST06-F06) should be that same shared connection, enabling real client
    cancellation of an in-progress mutation; see `_run_gateway_call`.
    """
    telemetry = OperationCounters()
    server = build_mcp_server(gateway, telemetry, db_lock=db_lock, connection=connection)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)
    return _StreamableHTTPASGIApp(session_manager), session_manager, telemetry


__all__ = ["build_mcp_server", "create_mcp_asgi_app", "TextContent", "json"]
