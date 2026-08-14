"""Use-case services: the single mutation boundary for the graph.

Every service validates against the domain model and current schema before delegating to
its repository. A future UI, MCP tool, or discovery agent must call these services rather
than a repository directly, so every mutation is validated the same way regardless of
caller (product principle: agent-native with accountability).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from personal_graph_os.application.activity_recording import MutationContext, record_activity_event
from personal_graph_os.application.repositories import (
    CanvasPlacementRepository,
    CanvasRepository,
    EdgeRepository,
    NodeRepository,
    ResearchSettingsRepository,
    ResourceRepository,
    SavedViewRepository,
    SearchIndexRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_dashboard import get_or_default_research_settings
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.semantic_keys import RESOURCE_NODE_TYPE_KEY
from personal_graph_os.domain.activity import MutationAction
from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.errors import (
    DomainError,
    SchemaEditConflictError,
    UnknownSchemaReferenceError,
)
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    CanvasId,
    CanvasPlacementId,
    EdgeId,
    EdgeTypeId,
    FieldDefinitionId,
    NodeId,
    NodeTypeId,
    ResourceId,
    SavedViewId,
    StatusDefinitionId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import (
    RepositoryLabel,
    Resource,
    ResourceKind,
    ResourceLifecycleStatus,
)
from personal_graph_os.domain.resource_identity import (
    ResourceIdentity,
    canonicalize_resource_identity,
)
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)
from personal_graph_os.domain.search import (
    SearchEntityType,
    SearchScope,
    build_node_search_text,
    build_resource_search_text,
    search_scope_for_resource_kind,
)
from personal_graph_os.domain.views import ProjectionQuery, SavedView, ViewKind


class WorkspaceNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a workspace that does not exist."""


class NodeNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a node that does not exist."""


class CanvasNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a canvas that does not exist."""


class PlacementNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a canvas placement that does not exist."""


class NodeTypeNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a node type that does not exist."""


class FieldDefinitionNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a field definition that does not exist."""


class StatusDefinitionNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a status definition that does not exist."""


class EdgeTypeNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references an edge type that does not exist."""


class EdgeNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references an edge that does not exist."""


class ResourceNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a resource that does not exist."""


class ResourceNodeTypeMissingError(UnknownSchemaReferenceError):
    """Raised when a workspace has no node type carrying the 'resource' semantic role yet.

    `ensure_semantic_schema()` (04.1) guarantees this on every bootstrap, so this only fires
    if a caller constructs a `ResourceService` against a workspace that skipped that step.
    """


class SavedViewNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a saved view that does not exist."""


def _validate_object_references(nodes: NodeRepository, node: Node, node_type: NodeType) -> None:
    """Reject a dangling `object_reference` field value.

    `FieldDefinition.validate_value()` is domain-only and has no repository access, so it can
    only check that the value is a non-empty string. Confirming the referenced node actually
    exists — and belongs to the same workspace — requires `NodeRepository`. A module-level
    function (not a method) so both `NodeService` (direct node writes) and `SchemaService`
    (which revalidates every existing node when a field is edited into `object_reference`) can
    reuse the identical check rather than one of them silently missing it.
    """
    for field_definition in node_type.field_definitions:
        if field_definition.field_type is not FieldType.OBJECT_REFERENCE:
            continue
        value = node.field_values.get(field_definition.id)
        if value is None:
            continue
        target = nodes.get(NodeId(str(value)))
        if target is None or target.workspace_id != node.workspace_id:
            raise UnknownSchemaReferenceError(
                f"field '{field_definition.name}' references node {value!r}, "
                f"which does not exist in workspace {node.workspace_id}"
            )


def _index_node_text(
    search_index: SearchIndexRepository | None,
    node: Node,
    node_type: NodeType,
    scope: SearchScope = SearchScope.GRAPH,
) -> None:
    """Keep `search_documents` current with a node's own title/body plus its searchable
    (`FieldType.TEXT`) custom-field values.

    A resource's identity/kind/takeaways/questions are indexed separately by `ResourceService`
    under the same `entity_id` (see `SqliteSearchIndexRepository`), so this never touches that
    text and a plain node write can never clobber it. `scope` places the row in the matching tab
    (default `graph` for generic nodes; resource/work-item/document services pass their own).
    """
    if search_index is None:
        return
    search_index.index_document(
        workspace_id=node.workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id=node.id,
        text=build_node_search_text(node, node_type),
        scope=scope,
    )


class NodeService:
    """The only path through which nodes are created and mutated."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def capture(self, workspace_id: WorkspaceId, node_type_id: NodeTypeId, title: str) -> Node:
        """Global quick capture: a title is the only required input. Atomically records this
        node's `ActivityEvent` (ST-07.2, decision #6/#8 `WORK.md`)."""
        node, node_type = self._build_captured_node(workspace_id, node_type_id, title)
        _validate_object_references(self._nodes, node, node_type)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.nodes.save_without_commit(node)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="node",
                entity_id=node.id,
                action=MutationAction.CREATED,
                after_state=node.model_dump(mode="json"),
            )
        _index_node_text(self._search_index, node, node_type)
        return node

    def _build_captured_node(
        self, workspace_id: WorkspaceId, node_type_id: NodeTypeId, title: str
    ) -> tuple[Node, NodeType]:
        workspace = self._require_workspace(workspace_id)
        node_type = workspace.node_type_by_id(node_type_id)
        if node_type is None:
            raise UnknownSchemaReferenceError(
                f"workspace {workspace_id} has no node type {node_type_id}"
            )
        node = Node(workspace_id=workspace_id, node_type_id=node_type_id, title=title)
        node.validate_against(node_type)
        return node, node_type

    def capture_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        title: str,
    ) -> Node:
        """Same validation/write as `capture`, into a caller-managed, already-open
        `unit_of_work` instead of committing on its own. The caller must index the result
        itself (`index_captured_node`) only after its own transaction has committed — see
        `ResourceService.create_or_reuse_within` for why indexing here would be premature."""
        node, node_type = self._build_captured_node(workspace_id, node_type_id, title)
        _validate_object_references(unit_of_work.nodes, node, node_type)
        unit_of_work.nodes.save_without_commit(node)
        return node

    def index_captured_node(self, node: Node) -> None:
        """Index `node` after a `*_within` write's transaction has committed."""
        workspace = self._require_workspace(node.workspace_id)
        node_type = workspace.node_type_by_id(node.node_type_id)
        if node_type is not None:
            _index_node_text(self._search_index, node, node_type)

    def update(
        self,
        node_id: NodeId,
        *,
        title: str | None = None,
        body: str | None = None,
        status_id: StatusDefinitionId | None = None,
        field_values: dict[str, object] | None = None,
    ) -> Node:
        existing = self._nodes.get(node_id)
        if existing is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        updated, node_type = self._build_updated_node(
            self._nodes,
            node_id,
            title=title,
            body=body,
            status_id=status_id,
            field_values=field_values,
        )
        _validate_object_references(self._nodes, updated, node_type)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.nodes.save_without_commit(updated)
            record_activity_event(
                unit_of_work,
                workspace_id=updated.workspace_id,
                context=MutationContext.rest(),
                entity_type="node",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
        _index_node_text(self._search_index, updated, node_type)
        return updated

    def update_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        node_id: NodeId,
        *,
        title: str | None = None,
        body: str | None = None,
        status_id: StatusDefinitionId | None = None,
        field_values: dict[str, object] | None = None,
    ) -> Node:
        """Same validation/write as `update`, into a caller-managed, already-open
        `unit_of_work`. Index the result with `index_captured_node` after commit."""
        updated, node_type = self._build_updated_node(
            unit_of_work.nodes,
            node_id,
            title=title,
            body=body,
            status_id=status_id,
            field_values=field_values,
        )
        _validate_object_references(unit_of_work.nodes, updated, node_type)
        unit_of_work.nodes.save_without_commit(updated)
        return updated

    def _build_updated_node(
        self,
        nodes: NodeRepository,
        node_id: NodeId,
        *,
        title: str | None,
        body: str | None,
        status_id: StatusDefinitionId | None,
        field_values: dict[str, object] | None,
    ) -> tuple[Node, NodeType]:
        node = nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        workspace = self._require_workspace(node.workspace_id)
        node_type = workspace.node_type_by_id(node.node_type_id)
        if node_type is None:
            raise UnknownSchemaReferenceError(
                f"workspace {node.workspace_id} has no node type {node.node_type_id}"
            )

        merged_field_values = (
            {**node.field_values, **field_values} if field_values is not None else node.field_values
        )
        # Reconstruct through the constructor (not `model_copy`) so every field validator,
        # including the title-non-empty check, reruns against the merged state.
        updated = Node(
            id=node.id,
            workspace_id=node.workspace_id,
            node_type_id=node.node_type_id,
            title=title if title is not None else node.title,
            body=body if body is not None else node.body,
            status_id=status_id if status_id is not None else node.status_id,
            field_values=merged_field_values,
            is_archived=node.is_archived,
            created_at=node.created_at,
            updated_at=datetime.now(UTC),
        )
        updated.validate_against(node_type)
        return updated, node_type

    def archive(self, node_id: NodeId) -> Node:
        existing = self._nodes.get(node_id)
        if existing is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        archived = self._build_archived_node(self._nodes, node_id)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.nodes.save_without_commit(archived)
            record_activity_event(
                unit_of_work,
                workspace_id=archived.workspace_id,
                context=MutationContext.rest(),
                entity_type="node",
                entity_id=archived.id,
                action=MutationAction.ARCHIVED,
                before_state=existing.model_dump(mode="json"),
                after_state=archived.model_dump(mode="json"),
            )
        return archived

    def archive_within(self, unit_of_work: ResearchUnitOfWork, node_id: NodeId) -> Node:
        """Same write as `archive`, into a caller-managed, already-open `unit_of_work`."""
        archived = self._build_archived_node(unit_of_work.nodes, node_id)
        unit_of_work.nodes.save_without_commit(archived)
        return archived

    def _build_archived_node(self, nodes: NodeRepository, node_id: NodeId) -> Node:
        node = nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        return Node(
            id=node.id,
            workspace_id=node.workspace_id,
            node_type_id=node.node_type_id,
            title=node.title,
            body=node.body,
            status_id=node.status_id,
            field_values=node.field_values,
            is_archived=True,
            created_at=node.created_at,
            updated_at=datetime.now(UTC),
        )

    def restore(self, node_id: NodeId) -> Node:
        """Un-archive a node (ST-09 MCP/REST lifecycle parity)."""
        existing = self._nodes.get(node_id)
        if existing is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        with self._unit_of_work_factory() as unit_of_work:
            restored = self.restore_within(unit_of_work, node_id)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="node",
                entity_id=existing.id,
                action=MutationAction.RESTORED,
                before_state=existing.model_dump(mode="json"),
                after_state=restored.model_dump(mode="json"),
            )
        return restored

    def restore_within(self, unit_of_work: ResearchUnitOfWork, node_id: NodeId) -> Node:
        """Same write as `restore`, into a caller-managed, already-open `unit_of_work`."""
        node = unit_of_work.nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        if not node.is_archived:
            return node
        restored = node.model_copy(update={"is_archived": False, "updated_at": datetime.now(UTC)})
        unit_of_work.nodes.save_without_commit(restored)
        return restored

    def delete(self, node_id: NodeId) -> None:
        """Hard-delete a node and everything that cascades from it (ST-09). Irrecoverable;
        callers must gate this behind an explicit destructive confirmation."""
        existing = self._nodes.get(node_id)
        if existing is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        with self._unit_of_work_factory() as unit_of_work:
            self.delete_within(unit_of_work, node_id)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="node",
                entity_id=existing.id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
        if self._search_index is not None:
            self._search_index.remove_document(entity_type=SearchEntityType.NODE, entity_id=node_id)

    def delete_within(self, unit_of_work: ResearchUnitOfWork, node_id: NodeId) -> None:
        """Same write as `delete`, into a caller-managed, already-open `unit_of_work`."""
        node = unit_of_work.nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        unit_of_work.nodes.delete_without_commit(node_id)

    def restore_snapshot_within(self, unit_of_work: ResearchUnitOfWork, target: Node) -> Node:
        """Write back an exact historical `Node` state (used only by undo's compensating
        restore, ST07-F04) after re-validating it against the *current* workspace schema --
        unlike `update_within`, which merges new fields, this reproduces a specific past
        snapshot, so the only thing left to check is that today's schema still accepts it.
        Raises `UnknownSchemaReferenceError` if the node type no longer exists or the
        snapshot no longer satisfies current field/status definitions."""
        workspace = self._require_workspace(target.workspace_id)
        node_type = workspace.node_type_by_id(target.node_type_id)
        if node_type is None:
            raise UnknownSchemaReferenceError(
                f"workspace {target.workspace_id} no longer has node type {target.node_type_id}"
            )
        target.validate_against(node_type)
        _validate_object_references(unit_of_work.nodes, target, node_type)
        unit_of_work.nodes.save_without_commit(target)
        return target


class EdgeService:
    """The only path through which typed edges are created.

    `connect` is REST's own atomic path (entity write + `ActivityEvent` in one
    `ResearchUnitOfWork`, ST-07.2); `connect_within` remains MCP's caller-managed variant."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._unit_of_work_factory = unit_of_work_factory

    def connect(
        self,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        source_node_id: NodeId,
        target_node_id: NodeId,
    ) -> Edge:
        edge = self._build_edge(
            self._nodes, workspace_id, edge_type_id, source_node_id, target_node_id
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.edges.save_without_commit(edge)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="edge",
                entity_id=edge.id,
                action=MutationAction.CREATED,
                after_state=edge.model_dump(mode="json"),
            )
        return edge

    def connect_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        source_node_id: NodeId,
        target_node_id: NodeId,
    ) -> Edge:
        """Same validation/write as `connect`, into a caller-managed, already-open
        `unit_of_work` instead of committing on its own."""
        edge = self._build_edge(
            unit_of_work.nodes, workspace_id, edge_type_id, source_node_id, target_node_id
        )
        unit_of_work.edges.save_without_commit(edge)
        return edge

    def disconnect(self, edge_id: EdgeId) -> None:
        """Hard-delete one edge (ST-09). Irrecoverable; callers must gate this behind an
        explicit destructive confirmation."""
        existing = self._edges.get(edge_id)
        if existing is None:
            raise EdgeNotFoundError(f"edge {edge_id} does not exist")
        with self._unit_of_work_factory() as unit_of_work:
            self.disconnect_within(unit_of_work, edge_id)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="edge",
                entity_id=existing.id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )

    def disconnect_within(self, unit_of_work: ResearchUnitOfWork, edge_id: EdgeId) -> None:
        """Same write as `disconnect`, into a caller-managed, already-open `unit_of_work`."""
        edge = unit_of_work.edges.get(edge_id)
        if edge is None:
            raise EdgeNotFoundError(f"edge {edge_id} does not exist")
        unit_of_work.edges.delete_without_commit(edge_id)

    def _build_edge(
        self,
        nodes: NodeRepository,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        source_node_id: NodeId,
        target_node_id: NodeId,
    ) -> Edge:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        if workspace.edge_type_by_id(edge_type_id) is None:
            raise UnknownSchemaReferenceError(
                f"workspace {workspace_id} has no edge type {edge_type_id}"
            )
        for node_id in (source_node_id, target_node_id):
            node = nodes.get(node_id)
            if node is None or node.workspace_id != workspace_id:
                raise NodeNotFoundError(
                    f"node {node_id} does not exist in workspace {workspace_id}"
                )

        return Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )


class CanvasService:
    """The only path through which canvases and node placements are created.

    Every mutation opens its own `ResearchUnitOfWork` so the entity write and its attributed
    `ActivityEvent` commit atomically (ST-07.2, decision #6/#8 `WORK.md`). REST is this
    service's only caller, so there is no separate `*_within` variant to keep in sync."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        canvases: CanvasRepository,
        placements: CanvasPlacementRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._canvases = canvases
        self._placements = placements
        self._unit_of_work_factory = unit_of_work_factory

    def create_canvas(self, workspace_id: WorkspaceId, name: str) -> Canvas:
        if self._workspaces.get(workspace_id) is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        canvas = Canvas(workspace_id=workspace_id, name=name)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.canvases.save_without_commit(canvas)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="canvas",
                entity_id=canvas.id,
                action=MutationAction.CREATED,
                after_state=canvas.model_dump(mode="json"),
            )
        return canvas

    def place_node(
        self,
        canvas_id: CanvasId,
        node_id: NodeId,
        *,
        position_x: float,
        position_y: float,
    ) -> CanvasPlacement:
        canvas = self._canvases.get(canvas_id)
        if canvas is None:
            raise CanvasNotFoundError(f"canvas {canvas_id} does not exist")
        node = self._nodes.get(node_id)
        if node is None or node.workspace_id != canvas.workspace_id:
            raise NodeNotFoundError(
                f"node {node_id} does not exist in canvas {canvas_id}'s workspace"
            )

        placement = CanvasPlacement(
            canvas_id=canvas_id, node_id=node_id, position_x=position_x, position_y=position_y
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.placements.save_without_commit(placement)
            record_activity_event(
                unit_of_work,
                workspace_id=canvas.workspace_id,
                context=MutationContext.rest(),
                entity_type="placement",
                entity_id=placement.id,
                action=MutationAction.CREATED,
                after_state=placement.model_dump(mode="json"),
            )
        return placement

    def update_placement(
        self,
        placement_id: CanvasPlacementId,
        *,
        position_x: float | None = None,
        position_y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        is_collapsed: bool | None = None,
    ) -> CanvasPlacement:
        """Move, resize, or collapse/expand a node's placement on one canvas.

        Reconstructs through the constructor (not `model_copy`) so the positive
        width/height invariant reruns against the merged state, matching `NodeService`.
        """
        placement = self._placements.get(placement_id)
        if placement is None:
            raise PlacementNotFoundError(f"canvas placement {placement_id} does not exist")
        canvas = self._canvases.get(placement.canvas_id)
        if canvas is None:
            raise CanvasNotFoundError(f"canvas {placement.canvas_id} does not exist")

        updated = CanvasPlacement(
            id=placement.id,
            canvas_id=placement.canvas_id,
            node_id=placement.node_id,
            position_x=position_x if position_x is not None else placement.position_x,
            position_y=position_y if position_y is not None else placement.position_y,
            width=width if width is not None else placement.width,
            height=height if height is not None else placement.height,
            is_collapsed=is_collapsed if is_collapsed is not None else placement.is_collapsed,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.placements.save_without_commit(updated)
            record_activity_event(
                unit_of_work,
                workspace_id=canvas.workspace_id,
                context=MutationContext.rest(),
                entity_type="placement",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=placement.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
        return updated


class SchemaService:
    """The only path through which user-editable schema is created and mutated.

    Schema is user data (product principle): node types, their field/status definitions,
    and edge types must be creatable and editable without a code change. Every node
    type/field/status change is guarded by revalidating every existing node of the
    affected type through `Node.validate_against()` — a rename is always safe, but
    narrowing a field's type, marking a field required, or removing a field/status still
    referenced by a node's data is rejected with `SchemaEditConflictError` naming the
    offending node, instead of silently corrupting that node's data. Removing a node type
    itself is deferred past this story: nodes reference it directly and no archival or
    reassignment workflow exists yet for that case.
    """

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def _require_node_type(self, workspace: Workspace, node_type_id: NodeTypeId) -> NodeType:
        node_type = workspace.node_type_by_id(node_type_id)
        if node_type is None:
            raise NodeTypeNotFoundError(f"workspace {workspace.id} has no node type {node_type_id}")
        return node_type

    def _require_edge_type(self, workspace: Workspace, edge_type_id: EdgeTypeId) -> EdgeType:
        edge_type = workspace.edge_type_by_id(edge_type_id)
        if edge_type is None:
            raise EdgeTypeNotFoundError(f"workspace {workspace.id} has no edge type {edge_type_id}")
        return edge_type

    def _replace_node_type_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace: Workspace,
        updated_node_type: NodeType,
    ) -> list[Node]:
        """Validate and save the schema change into `unit_of_work`; returns the affected
        nodes so the caller can reindex them once the transaction has committed."""
        affected_nodes = [
            node
            for node in unit_of_work.nodes.list_by_workspace(workspace.id, include_archived=True)
            if node.node_type_id == updated_node_type.id
        ]
        for node in affected_nodes:
            try:
                node.validate_against(updated_node_type)
                # Reuses the same repository-backed check `NodeService` runs on every direct
                # write, so converting a field to `object_reference` (or narrowing one that
                # already is) can't silently persist a value that no longer names a real,
                # same-workspace node — the schema-edit path was the one place this was missing.
                _validate_object_references(unit_of_work.nodes, node, updated_node_type)
            except DomainError as error:
                raise SchemaEditConflictError(
                    f"cannot apply schema change: node '{node.title}' ({node.id}) "
                    f"would become invalid: {error}"
                ) from error

        remaining_node_types = tuple(
            nt for nt in workspace.node_types if nt.id != updated_node_type.id
        )
        updated_workspace = Workspace(
            id=workspace.id,
            name=workspace.name,
            created_at=workspace.created_at,
            node_types=(*remaining_node_types, updated_node_type),
            edge_types=workspace.edge_types,
        )
        unit_of_work.workspaces.save_without_commit(updated_workspace)
        return affected_nodes

    def _reindex_affected_nodes(self, affected_nodes: list[Node], node_type: NodeType) -> None:
        # A field's type moving into/out of `FieldType.TEXT` (or a TEXT field being removed)
        # changes what every existing node of this type should contribute to search — reindex
        # them against the now-canonical schema, after the transaction commits, so search
        # stays immediately consistent instead of only catching up on the next node write.
        if self._search_index is None:
            return
        for node in affected_nodes:
            self._search_index.index_document(
                workspace_id=node.workspace_id,
                entity_type=SearchEntityType.NODE,
                entity_id=node.id,
                text=build_node_search_text(node, node_type),
            )

    def _replace_edge_type_within(
        self, unit_of_work: ResearchUnitOfWork, workspace: Workspace, updated_edge_type: EdgeType
    ) -> None:
        remaining_edge_types = tuple(
            et for et in workspace.edge_types if et.id != updated_edge_type.id
        )
        updated_workspace = Workspace(
            id=workspace.id,
            name=workspace.name,
            created_at=workspace.created_at,
            node_types=workspace.node_types,
            edge_types=(*remaining_edge_types, updated_edge_type),
        )
        unit_of_work.workspaces.save_without_commit(updated_workspace)

    # --- Node types -------------------------------------------------------------------

    def _record_node_type_event(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        action: MutationAction,
        before: NodeType | None,
        after: NodeType | None,
    ) -> None:
        record_activity_event(
            unit_of_work,
            workspace_id=workspace_id,
            context=MutationContext.rest(),
            entity_type="node_type",
            entity_id=node_type_id,
            action=action,
            before_state=before.model_dump(mode="json") if before is not None else None,
            after_state=after.model_dump(mode="json") if after is not None else None,
        )

    def create_node_type(
        self,
        workspace_id: WorkspaceId,
        name: str,
        *,
        icon: str = "circle",
        color_hex: str = "#6b7280",
    ) -> NodeType:
        workspace = self._require_workspace(workspace_id)
        node_type = NodeType(name=name, icon=icon, color_hex=color_hex)
        updated_workspace = Workspace(
            id=workspace.id,
            name=workspace.name,
            created_at=workspace.created_at,
            node_types=(*workspace.node_types, node_type),
            edge_types=workspace.edge_types,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.workspaces.save_without_commit(updated_workspace)
            self._record_node_type_event(
                unit_of_work,
                workspace_id=workspace_id,
                node_type_id=node_type.id,
                action=MutationAction.CREATED,
                before=None,
                after=node_type,
            )
        return node_type

    def update_node_type(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        *,
        name: str | None = None,
        icon: str | None = None,
        color_hex: str | None = None,
    ) -> NodeType:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        updated = NodeType(
            id=node_type.id,
            name=name if name is not None else node_type.name,
            icon=icon if icon is not None else node_type.icon,
            color_hex=color_hex if color_hex is not None else node_type.color_hex,
            field_definitions=node_type.field_definitions,
            status_definitions=node_type.status_definitions,
            # `system_key` is not a mutation parameter: preserving it here (rather than
            # dropping to the constructor default of `None`) is what makes it immutable
            # through this API while every other schema-edit field stays editable.
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(unit_of_work, workspace, updated)
            self._record_node_type_event(
                unit_of_work,
                workspace_id=workspace_id,
                node_type_id=node_type.id,
                action=MutationAction.UPDATED,
                before=node_type,
                after=updated,
            )
        self._reindex_affected_nodes(affected_nodes, updated)
        return updated

    # --- Field definitions ------------------------------------------------------------

    def add_field_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        name: str,
        field_type: FieldType,
        *,
        is_required: bool = False,
        select_options: tuple[str, ...] = (),
        description: str | None = None,
    ) -> FieldDefinition:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        field_definition = FieldDefinition(
            name=name,
            field_type=field_type,
            is_required=is_required,
            select_options=select_options,
            description=description,
        )
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=(*node_type.field_definitions, field_definition),
            status_definitions=node_type.status_definitions,
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="field_definition",
                entity_id=field_definition.id,
                action=MutationAction.CREATED,
                after_state=field_definition.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)
        return field_definition

    def update_field_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        field_definition_id: FieldDefinitionId,
        *,
        name: str | None = None,
        field_type: FieldType | None = None,
        is_required: bool | None = None,
        select_options: tuple[str, ...] | None = None,
        description: str | None = None,
        clear_description: bool = False,
    ) -> FieldDefinition:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        existing = node_type.field_by_id(field_definition_id)
        if existing is None:
            raise FieldDefinitionNotFoundError(
                f"node type '{node_type.name}' has no field {field_definition_id}"
            )
        updated_field = FieldDefinition(
            id=existing.id,
            name=name if name is not None else existing.name,
            field_type=field_type if field_type is not None else existing.field_type,
            is_required=is_required if is_required is not None else existing.is_required,
            select_options=(
                select_options if select_options is not None else existing.select_options
            ),
            description=(
                None
                if clear_description
                else (description if description is not None else existing.description)
            ),
        )
        remaining = tuple(f for f in node_type.field_definitions if f.id != existing.id)
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=(*remaining, updated_field),
            status_definitions=node_type.status_definitions,
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="field_definition",
                entity_id=updated_field.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated_field.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)
        return updated_field

    def remove_field_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        field_definition_id: FieldDefinitionId,
    ) -> None:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        existing = node_type.field_by_id(field_definition_id)
        if existing is None:
            raise FieldDefinitionNotFoundError(
                f"node type '{node_type.name}' has no field {field_definition_id}"
            )
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=tuple(
                f for f in node_type.field_definitions if f.id != field_definition_id
            ),
            status_definitions=node_type.status_definitions,
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="field_definition",
                entity_id=field_definition_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)

    # --- Status definitions ------------------------------------------------------------

    def add_status_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        name: str,
        *,
        color_hex: str = "#6b7280",
        is_terminal: bool = False,
        sort_order: int = 0,
    ) -> StatusDefinition:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        status_definition = StatusDefinition(
            name=name, color_hex=color_hex, is_terminal=is_terminal, sort_order=sort_order
        )
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=node_type.field_definitions,
            status_definitions=(*node_type.status_definitions, status_definition),
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="status_definition",
                entity_id=status_definition.id,
                action=MutationAction.CREATED,
                after_state=status_definition.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)
        return status_definition

    def update_status_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        status_definition_id: StatusDefinitionId,
        *,
        name: str | None = None,
        color_hex: str | None = None,
        is_terminal: bool | None = None,
        sort_order: int | None = None,
    ) -> StatusDefinition:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        existing = node_type.status_by_id(status_definition_id)
        if existing is None:
            raise StatusDefinitionNotFoundError(
                f"node type '{node_type.name}' has no status {status_definition_id}"
            )
        updated_status = StatusDefinition(
            id=existing.id,
            name=name if name is not None else existing.name,
            color_hex=color_hex if color_hex is not None else existing.color_hex,
            is_terminal=is_terminal if is_terminal is not None else existing.is_terminal,
            sort_order=sort_order if sort_order is not None else existing.sort_order,
        )
        remaining = tuple(s for s in node_type.status_definitions if s.id != existing.id)
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=node_type.field_definitions,
            status_definitions=(*remaining, updated_status),
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="status_definition",
                entity_id=updated_status.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated_status.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)
        return updated_status

    def remove_status_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        status_definition_id: StatusDefinitionId,
    ) -> None:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        existing = node_type.status_by_id(status_definition_id)
        if existing is None:
            raise StatusDefinitionNotFoundError(
                f"node type '{node_type.name}' has no status {status_definition_id}"
            )
        updated_node_type = NodeType(
            id=node_type.id,
            name=node_type.name,
            icon=node_type.icon,
            color_hex=node_type.color_hex,
            field_definitions=node_type.field_definitions,
            status_definitions=tuple(
                s for s in node_type.status_definitions if s.id != status_definition_id
            ),
            system_key=node_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            affected_nodes = self._replace_node_type_within(
                unit_of_work, workspace, updated_node_type
            )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="status_definition",
                entity_id=status_definition_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
        self._reindex_affected_nodes(affected_nodes, updated_node_type)

    # --- Edge types ---------------------------------------------------------------------

    def create_edge_type(
        self,
        workspace_id: WorkspaceId,
        name: str,
        *,
        inverse_name: str | None = None,
        color_hex: str = "#6b7280",
    ) -> EdgeType:
        workspace = self._require_workspace(workspace_id)
        edge_type = EdgeType(name=name, inverse_name=inverse_name, color_hex=color_hex)
        updated_workspace = Workspace(
            id=workspace.id,
            name=workspace.name,
            created_at=workspace.created_at,
            node_types=workspace.node_types,
            edge_types=(*workspace.edge_types, edge_type),
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.workspaces.save_without_commit(updated_workspace)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="edge_type",
                entity_id=edge_type.id,
                action=MutationAction.CREATED,
                after_state=edge_type.model_dump(mode="json"),
            )
        return edge_type

    def update_edge_type(
        self,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        *,
        name: str | None = None,
        inverse_name: str | None = None,
        clear_inverse_name: bool = False,
        color_hex: str | None = None,
    ) -> EdgeType:
        workspace = self._require_workspace(workspace_id)
        edge_type = self._require_edge_type(workspace, edge_type_id)
        updated = EdgeType(
            id=edge_type.id,
            name=name if name is not None else edge_type.name,
            inverse_name=(
                None
                if clear_inverse_name
                else (inverse_name if inverse_name is not None else edge_type.inverse_name)
            ),
            color_hex=color_hex if color_hex is not None else edge_type.color_hex,
            system_key=edge_type.system_key,
        )
        with self._unit_of_work_factory() as unit_of_work:
            self._replace_edge_type_within(unit_of_work, workspace, updated)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="edge_type",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=edge_type.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
        return updated

    def remove_edge_type(self, workspace_id: WorkspaceId, edge_type_id: EdgeTypeId) -> None:
        workspace = self._require_workspace(workspace_id)
        edge_type = self._require_edge_type(workspace, edge_type_id)
        for edge in self._edges.list_by_workspace(workspace_id):
            if edge.edge_type_id == edge_type_id:
                raise SchemaEditConflictError(
                    f"cannot remove edge type {edge_type_id}: edge {edge.id} still uses it"
                )
        remaining_edge_types = tuple(et for et in workspace.edge_types if et.id != edge_type_id)
        updated_workspace = Workspace(
            id=workspace.id,
            name=workspace.name,
            created_at=workspace.created_at,
            node_types=workspace.node_types,
            edge_types=remaining_edge_types,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.workspaces.save_without_commit(updated_workspace)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="edge_type",
                entity_id=edge_type_id,
                action=MutationAction.DELETED,
                before_state=edge_type.model_dump(mode="json"),
            )


def new_workspace(name: str) -> Workspace:
    """Construct a new, schema-empty workspace. Seeding default schema is a separate step."""
    return Workspace(id=WorkspaceId(new_id()), name=name)


class ResourceService:
    """The only path through which research resources (papers, repos, docs, ...) are
    created, deduplicated, and mutated.

    A `Resource` always backs one `Node` of the workspace's `system_key="resource"` node
    type; the two are created together, atomically, through `unit_of_work_factory` (04.1's
    `ResearchUnitOfWork`) so a failure never leaves an orphaned `Node` with no `Resource`.
    """

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        resources: ResourceRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._resources = resources
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    def _index_resource_text(self, resource: Resource) -> None:
        if self._search_index is None:
            return
        self._search_index.index_document(
            workspace_id=resource.workspace_id,
            entity_type=SearchEntityType.RESOURCE,
            entity_id=resource.node_id,
            text=build_resource_search_text(resource),
            scope=search_scope_for_resource_kind(resource.kind),
        )

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def _require_resource_node_type(self, workspace: Workspace) -> NodeType:
        node_type = workspace.node_type_by_system_key(RESOURCE_NODE_TYPE_KEY)
        if node_type is None:
            raise ResourceNodeTypeMissingError(
                f"workspace {workspace.id} has no node type carrying the 'resource' role"
            )
        return node_type

    def _require_resource(self, resource_id: ResourceId) -> Resource:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        return resource

    def create_or_reuse(
        self,
        workspace_id: WorkspaceId,
        title: str,
        raw_source: str,
        *,
        kind: ResourceKind | None = None,
        body: str = "",
    ) -> tuple[Resource, bool]:
        """Canonicalize `raw_source` and either reuse the matching resource or create one.

        Returns `(resource, was_created)`. A duplicate import never creates a second `Node`;
        it may enrich the existing resource's `source_url` if that was previously unset.
        """
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_resource_node_type(workspace)
        identity = canonicalize_resource_identity(raw_source)
        enriched: Resource | None = None
        with self._unit_of_work_factory() as unit_of_work:
            resource, was_created, node = self._write_resource(
                unit_of_work, workspace, node_type, title, identity, kind=kind, body=body
            )
            if was_created:
                record_activity_event(
                    unit_of_work,
                    workspace_id=workspace_id,
                    context=MutationContext.rest(),
                    entity_type="resource",
                    entity_id=resource.id,
                    action=MutationAction.CREATED,
                    after_state=resource.model_dump(mode="json"),
                )
            else:
                # Enrichment is a real mutation (it can set a previously-empty `source_url`),
                # so -- unlike a pure reuse no-op -- it commits atomically with exactly one
                # `ActivityEvent`, inside the same unit of work as the reuse lookup (ST07-F08).
                enriched = self._enrich_source_url_within(
                    unit_of_work, resource, identity.normalized_source_url
                )
                if enriched is not None:
                    record_activity_event(
                        unit_of_work,
                        workspace_id=workspace_id,
                        context=MutationContext.rest(),
                        entity_type="resource",
                        entity_id=resource.id,
                        action=MutationAction.UPDATED,
                        before_state=resource.model_dump(mode="json"),
                        after_state=enriched.model_dump(mode="json"),
                    )
        if was_created:
            assert node is not None
            self.index_created_resource(resource, node)
            return resource, True

        if enriched is not None:
            self.index_updated_resource(enriched)
            return enriched, False
        return resource, False

    def create_or_reuse_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        title: str,
        raw_source: str,
        *,
        kind: ResourceKind | None = None,
        body: str = "",
    ) -> tuple[Resource, bool, Node | None]:
        """Same write path as `create_or_reuse`, but into a caller-managed, already-open
        `unit_of_work` (typically inside a caller-managed `savepoint()`) instead of opening its
        own transaction. Used by `DiscoveryService` so a batch import's per-candidate writes
        live inside one nested savepoint rather than each candidate committing independently.

        Returns `(resource, was_created, node)`; `node` is `None` on reuse. Callers must index
        the result themselves via `index_created_resource` only after their own transaction has
        actually committed — indexing here would commit the caller's transaction early (see
        `index_created_resource`). Does not enrich an existing resource's `source_url` on reuse;
        that stays `create_or_reuse`'s job only, to keep this path's side effects minimal.
        """
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_resource_node_type(workspace)
        identity = canonicalize_resource_identity(raw_source)
        return self._write_resource(
            unit_of_work, workspace, node_type, title, identity, kind=kind, body=body
        )

    def index_created_resource(self, resource: Resource, node: Node) -> None:
        """Index a `(resource, node)` pair created via `create_or_reuse_within`.

        Call only after the unit of work that wrote them has committed: `SqliteSearchIndexRepository
        .index_document` commits its own connection, which would end a still-open caller
        transaction early if called beforehand.
        """
        workspace = self._require_workspace(resource.workspace_id)
        node_type = self._require_resource_node_type(workspace)
        _index_node_text(
            self._search_index,
            node,
            node_type,
            scope=search_scope_for_resource_kind(resource.kind),
        )
        self._index_resource_text(resource)

    def _write_resource(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace: Workspace,
        node_type: NodeType,
        title: str,
        identity: ResourceIdentity,
        *,
        kind: ResourceKind | None,
        body: str,
    ) -> tuple[Resource, bool, Node | None]:
        existing = self._resources.get_by_canonical_identifier(
            workspace.id, identity.canonical_identifier
        )
        if existing is not None:
            return existing, False, None

        resolved_kind = identity.detected_kind or kind or ResourceKind.OTHER
        node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title=title, body=body)
        node.validate_against(node_type)
        resource = Resource(
            workspace_id=workspace.id,
            node_id=node.id,
            kind=resolved_kind,
            canonical_identifier=identity.canonical_identifier,
            source_url=identity.normalized_source_url,
        )
        unit_of_work.nodes.save_without_commit(node)
        unit_of_work.resources.save_without_commit(resource)
        return resource, True, node

    def _enrich_source_url_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        existing: Resource,
        normalized_source_url: str | None,
    ) -> Resource | None:
        """Set a previously-empty `source_url` on a reused resource, atomically with the
        caller's `ActivityEvent` write (ST07-F08). Returns `None` when there is nothing to
        enrich, so the caller can skip recording an event for a genuine no-op reuse."""
        if existing.source_url is not None or normalized_source_url is None:
            return None
        enriched = existing.model_copy(update={"source_url": normalized_source_url})
        unit_of_work.resources.save_without_commit(enriched)
        return enriched

    def get(self, resource_id: ResourceId) -> Resource:
        return self._require_resource(resource_id)

    def list_by_workspace(
        self,
        workspace_id: WorkspaceId,
        *,
        kind: ResourceKind | None = None,
        lifecycle_status: ResourceLifecycleStatus | None = None,
        repository_label: RepositoryLabel | None = None,
        last_activity_since: datetime | None = None,
        last_activity_until: datetime | None = None,
    ) -> tuple[Resource, ...]:
        """List a workspace's resources, optionally bounded by the Research/Repositories
        workspace filters (EP-2026-012 ST-06): kind (type), lifecycle_status (read state),
        repository_label (ownership grouping), and a `last_activity_at` date range."""
        return self._resources.list_by_workspace(
            workspace_id,
            kind=kind,
            lifecycle_status=lifecycle_status,
            repository_label=repository_label,
            last_activity_since=last_activity_since,
            last_activity_until=last_activity_until,
        )

    def update(
        self,
        resource_id: ResourceId,
        *,
        lifecycle_status: ResourceLifecycleStatus | None = None,
        next_action: str | None = None,
        clear_next_action: bool = False,
        next_action_dismissed: bool | None = None,
        open_questions: tuple[str, ...] | None = None,
        takeaways: tuple[str, ...] | None = None,
        progress_percent: int | None = None,
        clear_progress_percent: bool = False,
        review_at: datetime | None = None,
        clear_review_at: bool = False,
        repository_label: RepositoryLabel | None = None,
        clear_repository_label: bool = False,
    ) -> Resource:
        """Update lifecycle/progress fields. `last_activity_at` advances only when the
        resulting state actually differs from the current one — a no-op call is not a
        "meaningful update", leaves the resource, including its timestamp, untouched, and
        never invents an `ActivityEvent` (ST-07.2, decision #6/#8 `WORK.md`)."""
        existing = self._resources.get(resource_id)
        if existing is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        updated = self._build_updated_resource(
            self._resources,
            resource_id,
            lifecycle_status=lifecycle_status,
            next_action=next_action,
            clear_next_action=clear_next_action,
            next_action_dismissed=next_action_dismissed,
            open_questions=open_questions,
            takeaways=takeaways,
            progress_percent=progress_percent,
            clear_progress_percent=clear_progress_percent,
            review_at=review_at,
            clear_review_at=clear_review_at,
            repository_label=repository_label,
            clear_repository_label=clear_repository_label,
        )
        was_modified = updated != existing
        if was_modified:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.resources.save_without_commit(updated)
                record_activity_event(
                    unit_of_work,
                    workspace_id=updated.workspace_id,
                    context=MutationContext.rest(),
                    entity_type="resource",
                    entity_id=updated.id,
                    action=MutationAction.UPDATED,
                    before_state=existing.model_dump(mode="json"),
                    after_state=updated.model_dump(mode="json"),
                )
            self._index_resource_text(updated)
        return updated

    def update_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        resource_id: ResourceId,
        *,
        lifecycle_status: ResourceLifecycleStatus | None = None,
        next_action: str | None = None,
        clear_next_action: bool = False,
        next_action_dismissed: bool | None = None,
        open_questions: tuple[str, ...] | None = None,
        takeaways: tuple[str, ...] | None = None,
        progress_percent: int | None = None,
        clear_progress_percent: bool = False,
        review_at: datetime | None = None,
        clear_review_at: bool = False,
        repository_label: RepositoryLabel | None = None,
        clear_repository_label: bool = False,
    ) -> tuple[Resource, bool]:
        """Same validation/write as `update`, into a caller-managed, already-open
        `unit_of_work`. Returns `(resource, was_modified)`: a no-op call skips the write
        entirely, so a caller recording an `ActivityEvent` for this mutation can skip that too
        rather than inventing one for a call that changed nothing. Index the result with
        `index_updated_resource` after commit, only when `was_modified` is true."""
        existing = unit_of_work.resources.get(resource_id)
        if existing is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        updated = self._build_updated_resource(
            unit_of_work.resources,
            resource_id,
            lifecycle_status=lifecycle_status,
            next_action=next_action,
            clear_next_action=clear_next_action,
            next_action_dismissed=next_action_dismissed,
            open_questions=open_questions,
            takeaways=takeaways,
            progress_percent=progress_percent,
            clear_progress_percent=clear_progress_percent,
            review_at=review_at,
            clear_review_at=clear_review_at,
            repository_label=repository_label,
            clear_repository_label=clear_repository_label,
        )
        was_modified = updated != existing
        if was_modified:
            unit_of_work.resources.save_without_commit(updated)
        return updated, was_modified

    def index_updated_resource(self, resource: Resource) -> None:
        """Index `resource` after a `*_within` write's transaction has committed."""
        self._index_resource_text(resource)

    def _build_updated_resource(
        self,
        resources: ResourceRepository,
        resource_id: ResourceId,
        *,
        lifecycle_status: ResourceLifecycleStatus | None,
        next_action: str | None,
        clear_next_action: bool,
        next_action_dismissed: bool | None,
        open_questions: tuple[str, ...] | None,
        takeaways: tuple[str, ...] | None,
        progress_percent: int | None,
        clear_progress_percent: bool,
        review_at: datetime | None,
        clear_review_at: bool,
        repository_label: RepositoryLabel | None,
        clear_repository_label: bool,
    ) -> Resource:
        existing = resources.get(resource_id)
        if existing is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        candidate = Resource(
            id=existing.id,
            workspace_id=existing.workspace_id,
            node_id=existing.node_id,
            kind=existing.kind,
            canonical_identifier=existing.canonical_identifier,
            source_url=existing.source_url,
            lifecycle_status=(
                lifecycle_status if lifecycle_status is not None else existing.lifecycle_status
            ),
            next_action=(
                None
                if clear_next_action
                else (next_action if next_action is not None else existing.next_action)
            ),
            next_action_dismissed=(
                next_action_dismissed
                if next_action_dismissed is not None
                else existing.next_action_dismissed
            ),
            open_questions=(
                open_questions if open_questions is not None else existing.open_questions
            ),
            takeaways=takeaways if takeaways is not None else existing.takeaways,
            progress_percent=(
                None
                if clear_progress_percent
                else (
                    progress_percent if progress_percent is not None else existing.progress_percent
                )
            ),
            review_at=(
                None
                if clear_review_at
                else (review_at if review_at is not None else existing.review_at)
            ),
            last_activity_at=existing.last_activity_at,
            repository_label=(
                None
                if clear_repository_label
                else (
                    repository_label if repository_label is not None else existing.repository_label
                )
            ),
        )
        if candidate == existing:
            return existing
        return candidate.model_copy(update={"last_activity_at": datetime.now(UTC)})

    def archive(self, resource_id: ResourceId) -> Resource:
        return self.update(resource_id, lifecycle_status=ResourceLifecycleStatus.ARCHIVED)

    def archive_within(
        self, unit_of_work: ResearchUnitOfWork, resource_id: ResourceId
    ) -> tuple[Resource, bool]:
        """Same write as `archive`, into a caller-managed, already-open `unit_of_work`."""
        return self.update_within(
            unit_of_work, resource_id, lifecycle_status=ResourceLifecycleStatus.ARCHIVED
        )

    def restore(self, resource_id: ResourceId) -> Resource:
        """Un-archive a resource (ST-09): the archive lifecycle does not remember the prior
        status, so restore returns it to `INBOX` — a neutral "needs attention" state — rather
        than guessing a prior stage. A no-op when the resource is not archived."""
        existing = self._resources.get(resource_id)
        if existing is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        if existing.lifecycle_status is not ResourceLifecycleStatus.ARCHIVED:
            return existing
        return self.update(resource_id, lifecycle_status=ResourceLifecycleStatus.INBOX)

    def restore_within(
        self, unit_of_work: ResearchUnitOfWork, resource_id: ResourceId
    ) -> tuple[Resource, bool]:
        """Same write as `restore`, into a caller-managed, already-open `unit_of_work`."""
        return self.update_within(
            unit_of_work, resource_id, lifecycle_status=ResourceLifecycleStatus.INBOX
        )

    def delete(self, resource_id: ResourceId) -> None:
        """Hard-delete a resource and its backing node together (ST-09). Irrecoverable;
        callers must gate this behind an explicit destructive confirmation."""
        existing = self._resources.get(resource_id)
        if existing is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        with self._unit_of_work_factory() as unit_of_work:
            self.delete_within(unit_of_work, resource_id)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="resource",
                entity_id=existing.id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
        if self._search_index is not None:
            self._search_index.remove_document(
                entity_type=SearchEntityType.RESOURCE, entity_id=resource_id
            )

    def delete_within(self, unit_of_work: ResearchUnitOfWork, resource_id: ResourceId) -> None:
        """Same write as `delete`, into a caller-managed, already-open `unit_of_work`."""
        resource = unit_of_work.resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        unit_of_work.resources.delete_without_commit(resource_id)
        unit_of_work.nodes.delete_without_commit(resource.node_id)

    def restore_snapshot_within(
        self, unit_of_work: ResearchUnitOfWork, target: Resource
    ) -> Resource:
        """Write back an exact historical `Resource` state (used only by undo's compensating
        restore, ST07-F04), after confirming the workspace still carries a resource-role node
        type -- the schema-changed-since-event conflict this aggregate can actually have."""
        workspace = self._require_workspace(target.workspace_id)
        self._require_resource_node_type(workspace)
        unit_of_work.resources.save_without_commit(target)
        return target


class SavedViewService:
    """The only path through which named, reusable table/Kanban/timeline projections are
    created and mutated.

    A `SavedView` never stores a raw filter/sort expression: `filters`/`sort` are typed
    `FilterClause`/`SortClause` values naming an allowlisted `FilterField`, and
    `SavedView.definitions_from_query()`/`to_projection_query()` are the only conversion
    between that typed shape and the persisted JSON columns (see `domain/views.py`).
    """

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        saved_views: SavedViewRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._saved_views = saved_views
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def _require_saved_view(self, saved_view_id: SavedViewId) -> SavedView:
        saved_view = self._saved_views.get(saved_view_id)
        if saved_view is None:
            raise SavedViewNotFoundError(f"saved view {saved_view_id} does not exist")
        return saved_view

    def create(
        self,
        workspace_id: WorkspaceId,
        name: str,
        view_kind: ViewKind,
        query: ProjectionQuery,
    ) -> SavedView:
        self._require_workspace(workspace_id)
        filter_definition, sort_definition = SavedView.definitions_from_query(query)
        saved_view = SavedView(
            workspace_id=workspace_id,
            name=name,
            view_kind=view_kind,
            filter_definition=filter_definition,
            sort_definition=sort_definition,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.saved_views.save_without_commit(saved_view)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="saved_view",
                entity_id=saved_view.id,
                action=MutationAction.CREATED,
                after_state=saved_view.model_dump(mode="json"),
            )
        return saved_view

    def update(
        self,
        saved_view_id: SavedViewId,
        *,
        name: str | None = None,
        query: ProjectionQuery | None = None,
    ) -> SavedView:
        existing = self._require_saved_view(saved_view_id)
        if query is not None:
            filter_definition, sort_definition = SavedView.definitions_from_query(query)
        else:
            filter_definition, sort_definition = (
                existing.filter_definition,
                existing.sort_definition,
            )
        updated = SavedView(
            id=existing.id,
            workspace_id=existing.workspace_id,
            name=name if name is not None else existing.name,
            view_kind=existing.view_kind,
            filter_definition=filter_definition,
            sort_definition=sort_definition,
            created_at=existing.created_at,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.saved_views.save_without_commit(updated)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="saved_view",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
        return updated

    def get(self, saved_view_id: SavedViewId) -> SavedView:
        return self._require_saved_view(saved_view_id)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[SavedView, ...]:
        return self._saved_views.list_by_workspace(workspace_id)

    def delete(self, saved_view_id: SavedViewId) -> None:
        existing = self._require_saved_view(saved_view_id)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.saved_views.delete_without_commit(saved_view_id)
            record_activity_event(
                unit_of_work,
                workspace_id=existing.workspace_id,
                context=MutationContext.rest(),
                entity_type="saved_view",
                entity_id=saved_view_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )


class ResearchSettingsService:
    """The only path through which per-workspace research resurfacing settings are read/set."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        research_settings: ResearchSettingsRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._research_settings = research_settings
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def get_or_default(self, workspace_id: WorkspaceId) -> WorkspaceResearchSettings:
        self._require_workspace(workspace_id)
        return get_or_default_research_settings(self._research_settings, workspace_id)

    def update(
        self, workspace_id: WorkspaceId, *, stale_after_days: int
    ) -> WorkspaceResearchSettings:
        self._require_workspace(workspace_id)
        existing = self._research_settings.get(workspace_id)
        settings = WorkspaceResearchSettings(
            workspace_id=workspace_id, stale_after_days=stale_after_days
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.research_settings.save_without_commit(settings)
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="research_settings",
                entity_id=workspace_id,
                action=MutationAction.CREATED if existing is None else MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json") if existing is not None else None,
                after_state=settings.model_dump(mode="json"),
            )
        return settings
