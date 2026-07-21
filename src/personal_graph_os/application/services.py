"""Use-case services: the single mutation boundary for the graph.

Every service validates against the domain model and current schema before delegating to
its repository. A future UI, MCP tool, or discovery agent must call these services rather
than a repository directly, so every mutation is validated the same way regardless of
caller (product principle: agent-native with accountability).
"""

from __future__ import annotations

from datetime import UTC, datetime

from personal_graph_os.application.repositories import (
    CanvasPlacementRepository,
    CanvasRepository,
    EdgeRepository,
    NodeRepository,
    WorkspaceRepository,
)
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
    EdgeTypeId,
    FieldDefinitionId,
    NodeId,
    NodeTypeId,
    StatusDefinitionId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)


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


class NodeService:
    """The only path through which nodes are created and mutated."""

    def __init__(self, workspaces: WorkspaceRepository, nodes: NodeRepository) -> None:
        self._workspaces = workspaces
        self._nodes = nodes

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def _validate_object_references(self, node: Node, node_type: NodeType) -> None:
        """Reject a dangling `object_reference` field value.

        `FieldDefinition.validate_value()` is domain-only and has no repository access, so it
        can only check that the value is a non-empty string. Confirming the referenced node
        actually exists — and belongs to the same workspace — requires `NodeRepository`, which
        only this application-layer service has.
        """
        for field_definition in node_type.field_definitions:
            if field_definition.field_type is not FieldType.OBJECT_REFERENCE:
                continue
            value = node.field_values.get(field_definition.id)
            if value is None:
                continue
            target = self._nodes.get(NodeId(str(value)))
            if target is None or target.workspace_id != node.workspace_id:
                raise UnknownSchemaReferenceError(
                    f"field '{field_definition.name}' references node {value!r}, "
                    f"which does not exist in workspace {node.workspace_id}"
                )

    def capture(self, workspace_id: WorkspaceId, node_type_id: NodeTypeId, title: str) -> Node:
        """Global quick capture: a title is the only required input."""
        workspace = self._require_workspace(workspace_id)
        node_type = workspace.node_type_by_id(node_type_id)
        if node_type is None:
            raise UnknownSchemaReferenceError(
                f"workspace {workspace_id} has no node type {node_type_id}"
            )
        node = Node(workspace_id=workspace_id, node_type_id=node_type_id, title=title)
        node.validate_against(node_type)
        self._validate_object_references(node, node_type)
        self._nodes.save(node)
        return node

    def update(
        self,
        node_id: NodeId,
        *,
        title: str | None = None,
        body: str | None = None,
        status_id: StatusDefinitionId | None = None,
        field_values: dict[str, object] | None = None,
    ) -> Node:
        node = self._nodes.get(node_id)
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
        self._validate_object_references(updated, node_type)
        self._nodes.save(updated)
        return updated

    def archive(self, node_id: NodeId) -> Node:
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        archived = Node(
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
        self._nodes.save(archived)
        return archived


class EdgeService:
    """The only path through which typed edges are created."""

    def __init__(
        self, workspaces: WorkspaceRepository, nodes: NodeRepository, edges: EdgeRepository
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges

    def connect(
        self,
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
            node = self._nodes.get(node_id)
            if node is None or node.workspace_id != workspace_id:
                raise NodeNotFoundError(
                    f"node {node_id} does not exist in workspace {workspace_id}"
                )

        edge = Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )
        self._edges.save(edge)
        return edge


class CanvasService:
    """The only path through which canvases and node placements are created."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        canvases: CanvasRepository,
        placements: CanvasPlacementRepository,
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._canvases = canvases
        self._placements = placements

    def create_canvas(self, workspace_id: WorkspaceId, name: str) -> Canvas:
        if self._workspaces.get(workspace_id) is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        canvas = Canvas(workspace_id=workspace_id, name=name)
        self._canvases.save(canvas)
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
        self._placements.save(placement)
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
        self._placements.save(updated)
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
        self, workspaces: WorkspaceRepository, nodes: NodeRepository, edges: EdgeRepository
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges

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

    def _replace_node_type(self, workspace: Workspace, updated_node_type: NodeType) -> NodeType:
        for node in self._nodes.list_by_workspace(workspace.id, include_archived=True):
            if node.node_type_id != updated_node_type.id:
                continue
            try:
                node.validate_against(updated_node_type)
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
        self._workspaces.save(updated_workspace)
        return updated_node_type

    def _replace_edge_type(self, workspace: Workspace, updated_edge_type: EdgeType) -> EdgeType:
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
        self._workspaces.save(updated_workspace)
        return updated_edge_type

    # --- Node types -------------------------------------------------------------------

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
        self._workspaces.save(updated_workspace)
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
        )
        return self._replace_node_type(workspace, updated)

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
        )
        self._replace_node_type(workspace, updated_node_type)
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
        )
        self._replace_node_type(workspace, updated_node_type)
        return updated_field

    def remove_field_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        field_definition_id: FieldDefinitionId,
    ) -> None:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        if node_type.field_by_id(field_definition_id) is None:
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
        )
        self._replace_node_type(workspace, updated_node_type)

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
        )
        self._replace_node_type(workspace, updated_node_type)
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
        )
        self._replace_node_type(workspace, updated_node_type)
        return updated_status

    def remove_status_definition(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        status_definition_id: StatusDefinitionId,
    ) -> None:
        workspace = self._require_workspace(workspace_id)
        node_type = self._require_node_type(workspace, node_type_id)
        if node_type.status_by_id(status_definition_id) is None:
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
        )
        self._replace_node_type(workspace, updated_node_type)

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
        self._workspaces.save(updated_workspace)
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
        )
        return self._replace_edge_type(workspace, updated)

    def remove_edge_type(self, workspace_id: WorkspaceId, edge_type_id: EdgeTypeId) -> None:
        workspace = self._require_workspace(workspace_id)
        self._require_edge_type(workspace, edge_type_id)
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
        self._workspaces.save(updated_workspace)


def new_workspace(name: str) -> Workspace:
    """Construct a new, schema-empty workspace. Seeding default schema is a separate step."""
    return Workspace(id=WorkspaceId(new_id()), name=name)
