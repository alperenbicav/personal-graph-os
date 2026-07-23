"""Local workspace bootstrap: ensure a default workspace and canvas exist.

The MVP runs a single local workspace. Any caller that needs one (the HTTP API today, an
MCP server later) can reuse this instead of re-implementing "get or create the first one."
"""

from __future__ import annotations

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.repositories import (
    CanvasRepository,
    NodeRepository,
    ResourceRepository,
    SearchIndexRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.canvas import Canvas
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.search import (
    SearchEntityType,
    build_node_search_text,
    build_resource_search_text,
)

DEFAULT_WORKSPACE_NAME = "Personal"
DEFAULT_CANVAS_NAME = "Main"


def get_or_create_default_workspace(workspaces: WorkspaceRepository) -> Workspace:
    """Get-or-create the single local workspace, then ensure its semantic workflow roles.

    `ensure_semantic_schema` is a no-op for a freshly seeded workspace (already stamped by
    `seed_default_schema`) and only does real work for one created before ST-04 introduced
    `system_key`; running it unconditionally keeps both paths correct without branching here.
    """
    existing = workspaces.list_all()
    if existing:
        workspace = existing[0]
    else:
        workspace = seed_default_schema(new_workspace(DEFAULT_WORKSPACE_NAME))
        workspaces.save(workspace)

    ensured = ensure_semantic_schema(workspace)
    if ensured != workspace:
        workspaces.save(ensured)
    return ensured


def get_or_create_default_canvas(canvases: CanvasRepository, workspace: Workspace) -> Canvas:
    existing = canvases.list_by_workspace(workspace.id)
    if existing:
        return existing[0]

    canvas = Canvas(workspace_id=workspace.id, name=DEFAULT_CANVAS_NAME)
    canvases.save(canvas)
    return canvas


def backfill_search_index(
    nodes: NodeRepository,
    resources: ResourceRepository,
    search_index: SearchIndexRepository,
    workspace: Workspace,
) -> None:
    """Index every existing node/resource so search covers data captured before ST-04.3.

    `SearchIndexRepository.index_document()` is delete-then-insert per `(entity_type,
    entity_id)`, so re-running this on every startup is idempotent and safe at the MVP's
    local, single-workspace scale rather than requiring a one-time migration flag.
    """
    for node in nodes.list_by_workspace(workspace.id, include_archived=True):
        node_type = workspace.node_type_by_id(node.node_type_id)
        if node_type is None:
            raise UnknownSchemaReferenceError(
                f"workspace {workspace.id} has no node type {node.node_type_id}"
            )
        search_index.index_document(
            workspace_id=node.workspace_id,
            entity_type=SearchEntityType.NODE,
            entity_id=node.id,
            text=build_node_search_text(node, node_type),
        )
    for resource in resources.list_by_workspace(workspace.id):
        search_index.index_document(
            workspace_id=resource.workspace_id,
            entity_type=SearchEntityType.RESOURCE,
            entity_id=resource.node_id,
            text=build_resource_search_text(resource),
        )
