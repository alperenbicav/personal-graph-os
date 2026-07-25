"""SQLite implementations of the application layer's repository protocols.

Each repository maps between the domain model and its normalized table(s). Nested schema
(field/status definitions under a node type) is reassembled on read; field/edge values
are stored as JSON since their shape is user-defined, not fixed by the relational schema.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from personal_graph_os.application.file_storage import PendingFileOperation
from personal_graph_os.domain.activity import (
    ActivityEvent,
    ActorKind,
    DiscoveredCandidate,
    DiscoveryRun,
    IdempotencyReceipt,
    MutationAction,
)
from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.documents import (
    Collection,
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
    DocumentVersion,
    Tag,
)
from personal_graph_os.domain.enrichment import (
    CitedEvidenceReference,
    ConcurrentEnrichmentUpdateError,
    ProposedRelationKind,
    RelationProposal,
    RelationProposalStatus,
    ResourceEnrichmentProfile,
    ResourceEnrichmentProfileVersion,
    payload_type_for_kind,
)
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    ActivityEventId,
    AttachmentId,
    CanvasId,
    CanvasPlacementId,
    CollectionId,
    ContextPackId,
    DiscoveryRunId,
    DocumentId,
    DocumentLinkId,
    DocumentVersionId,
    EdgeId,
    FileReferenceId,
    IdempotencyReceiptId,
    IngestionJobId,
    NodeId,
    RelationProposalId,
    ResourceEnrichmentProfileId,
    ResourceEnrichmentProfileVersionId,
    ResourceId,
    SavedViewId,
    TagId,
    WorkItemId,
    WorkPlanningReceiptId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.ingestion import IngestionJob, IngestionJobStatus, IngestionStage
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import (
    RepositoryLabel,
    Resource,
    ResourceKind,
    ResourceLifecycleStatus,
)
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    NodeType,
    StatusDefinition,
    Workspace,
)
from personal_graph_os.domain.search import SearchEntityType, SearchHit, compile_fts5_query
from personal_graph_os.domain.views import ContextPack, SavedView, ViewKind
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
    WorkItemType,
)
from personal_graph_os.domain.work_planning import WorkPlanningReceipt, WorkPlanOutcomeStatus


class SqliteWorkspaceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        row = self._connection.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate(row)

    def list_all(self) -> tuple[Workspace, ...]:
        rows = self._connection.execute("SELECT * FROM workspaces").fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, workspace: Workspace) -> None:
        with self._connection:
            self.save_without_commit(workspace)

    def save_without_commit(self, workspace: Workspace) -> None:
        self._connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            (workspace.id, workspace.name, workspace.created_at.isoformat()),
        )
        self._save_node_types(workspace)
        self._save_edge_types(workspace)

    def _save_node_types(self, workspace: Workspace) -> None:
        for node_type in workspace.node_types:
            self._connection.execute(
                "INSERT INTO node_types (id, workspace_id, name, icon, color_hex, system_key) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, icon = excluded.icon, "
                "color_hex = excluded.color_hex, system_key = excluded.system_key",
                (
                    node_type.id,
                    workspace.id,
                    node_type.name,
                    node_type.icon,
                    node_type.color_hex,
                    node_type.system_key,
                ),
            )
            # `save()` is a full-replace upsert: a field/status definition no longer present
            # on `node_type` must be deleted here, or it would silently resurrect on the next
            # `_hydrate_node_type()` read (which selects every row still in the table).
            self._delete_stale_children(
                table="field_definitions",
                parent_column="node_type_id",
                parent_id=node_type.id,
                current_ids=tuple(f.id for f in node_type.field_definitions),
            )
            self._delete_stale_children(
                table="status_definitions",
                parent_column="node_type_id",
                parent_id=node_type.id,
                current_ids=tuple(s.id for s in node_type.status_definitions),
            )
            for field_definition in node_type.field_definitions:
                self._connection.execute(
                    "INSERT INTO field_definitions "
                    "(id, node_type_id, name, field_type, is_required, "
                    " select_options_json, description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                    "field_type = excluded.field_type, is_required = excluded.is_required, "
                    "select_options_json = excluded.select_options_json, "
                    "description = excluded.description",
                    (
                        field_definition.id,
                        node_type.id,
                        field_definition.name,
                        field_definition.field_type.value,
                        int(field_definition.is_required),
                        json.dumps(list(field_definition.select_options)),
                        field_definition.description,
                    ),
                )
            for status_definition in node_type.status_definitions:
                self._connection.execute(
                    "INSERT INTO status_definitions "
                    "(id, node_type_id, name, color_hex, is_terminal, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                    "color_hex = excluded.color_hex, is_terminal = excluded.is_terminal, "
                    "sort_order = excluded.sort_order",
                    (
                        status_definition.id,
                        node_type.id,
                        status_definition.name,
                        status_definition.color_hex,
                        int(status_definition.is_terminal),
                        status_definition.sort_order,
                    ),
                )

    def _delete_stale_children(
        self,
        *,
        table: str,
        parent_column: str,
        parent_id: str,
        current_ids: tuple[str, ...],
    ) -> None:
        """Delete rows under `parent_id` whose id is no longer in `current_ids`.

        `table`/`parent_column` are always the fixed literals passed by call sites in this
        module, never caller-supplied input, so interpolating them into the statement carries
        no injection risk; only `current_ids`/`parent_id` values are ever bound as parameters.
        """
        if current_ids:
            placeholders = ",".join("?" for _ in current_ids)
            self._connection.execute(
                f"DELETE FROM {table} WHERE {parent_column} = ? AND id NOT IN ({placeholders})",
                (parent_id, *current_ids),
            )
        else:
            self._connection.execute(f"DELETE FROM {table} WHERE {parent_column} = ?", (parent_id,))

    def _save_edge_types(self, workspace: Workspace) -> None:
        self._delete_stale_children(
            table="edge_types",
            parent_column="workspace_id",
            parent_id=workspace.id,
            current_ids=tuple(et.id for et in workspace.edge_types),
        )
        for edge_type in workspace.edge_types:
            self._connection.execute(
                "INSERT INTO edge_types "
                "(id, workspace_id, name, inverse_name, color_hex, system_key) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                "inverse_name = excluded.inverse_name, color_hex = excluded.color_hex, "
                "system_key = excluded.system_key",
                (
                    edge_type.id,
                    workspace.id,
                    edge_type.name,
                    edge_type.inverse_name,
                    edge_type.color_hex,
                    edge_type.system_key,
                ),
            )

    def _hydrate(self, workspace_row: sqlite3.Row) -> Workspace:
        node_type_rows = self._connection.execute(
            "SELECT * FROM node_types WHERE workspace_id = ?", (workspace_row["id"],)
        ).fetchall()
        node_types = tuple(self._hydrate_node_type(row) for row in node_type_rows)

        edge_type_rows = self._connection.execute(
            "SELECT * FROM edge_types WHERE workspace_id = ?", (workspace_row["id"],)
        ).fetchall()
        edge_types = tuple(
            EdgeType(
                id=row["id"],
                name=row["name"],
                inverse_name=row["inverse_name"],
                color_hex=row["color_hex"],
                system_key=row["system_key"],
            )
            for row in edge_type_rows
        )

        return Workspace(
            id=workspace_row["id"],
            name=workspace_row["name"],
            created_at=datetime.fromisoformat(workspace_row["created_at"]),
            node_types=node_types,
            edge_types=edge_types,
        )

    def _hydrate_node_type(self, node_type_row: sqlite3.Row) -> NodeType:
        field_rows = self._connection.execute(
            "SELECT * FROM field_definitions WHERE node_type_id = ?", (node_type_row["id"],)
        ).fetchall()
        field_definitions = tuple(
            FieldDefinition(
                id=row["id"],
                name=row["name"],
                field_type=row["field_type"],
                is_required=bool(row["is_required"]),
                select_options=tuple(json.loads(row["select_options_json"])),
                description=row["description"],
            )
            for row in field_rows
        )

        status_rows = self._connection.execute(
            "SELECT * FROM status_definitions WHERE node_type_id = ?", (node_type_row["id"],)
        ).fetchall()
        status_definitions = tuple(
            StatusDefinition(
                id=row["id"],
                name=row["name"],
                color_hex=row["color_hex"],
                is_terminal=bool(row["is_terminal"]),
                sort_order=row["sort_order"],
            )
            for row in status_rows
        )

        return NodeType(
            id=node_type_row["id"],
            name=node_type_row["name"],
            icon=node_type_row["icon"],
            color_hex=node_type_row["color_hex"],
            field_definitions=field_definitions,
            status_definitions=status_definitions,
            system_key=node_type_row["system_key"],
        )


class SqliteNodeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, node_id: NodeId) -> Node | None:
        row = self._connection.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(
        self, workspace_id: WorkspaceId, *, include_archived: bool = False
    ) -> tuple[Node, ...]:
        if include_archived:
            rows = self._connection.execute(
                "SELECT * FROM nodes WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM nodes WHERE workspace_id = ? AND is_archived = 0", (workspace_id,)
            ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, node: Node) -> None:
        with self._connection:
            self.save_without_commit(node)

    def save_without_commit(self, node: Node) -> None:
        """Write `node`'s row without committing.

        Exists so `SqliteResearchUnitOfWork` can write a `Node` and a `Resource` inside one
        externally controlled transaction; `save()` above is the normal auto-committing path
        every other caller uses.
        """
        self._connection.execute(
            "INSERT INTO nodes "
            "(id, workspace_id, node_type_id, title, body, status_id, field_values_json, "
            " is_archived, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET title = excluded.title, body = excluded.body, "
            "status_id = excluded.status_id, field_values_json = excluded.field_values_json, "
            "is_archived = excluded.is_archived, updated_at = excluded.updated_at",
            (
                node.id,
                node.workspace_id,
                node.node_type_id,
                node.title,
                node.body,
                node.status_id,
                json.dumps(node.field_values),
                int(node.is_archived),
                node.created_at.isoformat(),
                node.updated_at.isoformat(),
            ),
        )

    def delete(self, node_id: NodeId) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM nodes WHERE id = ?", (node_id,))

    def _hydrate(self, row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"],
            workspace_id=row["workspace_id"],
            node_type_id=row["node_type_id"],
            title=row["title"],
            body=row["body"],
            status_id=row["status_id"],
            field_values=json.loads(row["field_values_json"]),
            is_archived=bool(row["is_archived"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteEdgeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, edge_id: EdgeId) -> Edge | None:
        row = self._connection.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[Edge, ...]:
        rows = self._connection.execute(
            "SELECT * FROM edges WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def list_incident_to_node(self, node_id: NodeId) -> tuple[Edge, ...]:
        rows = self._connection.execute(
            "SELECT * FROM edges WHERE source_node_id = ? OR target_node_id = ?",
            (node_id, node_id),
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, edge: Edge) -> None:
        with self._connection:
            self.save_without_commit(edge)

    def save_if_absent_without_commit(self, edge: Edge) -> bool:
        """Insert `edge` atomically only if no edge with the same `(workspace_id, edge_type_id,
        source_node_id, target_node_id)` already exists (review finding S4-R05), enforced by the
        `idx_edges_canonical_uniqueness` unique index (migration 0011) rather than a
        non-atomic read-then-insert. Returns whether a new row was actually inserted."""
        cursor = self._connection.execute(
            "INSERT INTO edges "
            "(id, workspace_id, edge_type_id, source_node_id, target_node_id, "
            " field_values_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (workspace_id, edge_type_id, source_node_id, target_node_id) DO NOTHING",
            (
                edge.id,
                edge.workspace_id,
                edge.edge_type_id,
                edge.source_node_id,
                edge.target_node_id,
                json.dumps(edge.field_values),
                edge.created_at.isoformat(),
            ),
        )
        return cursor.rowcount > 0

    def save_without_commit(self, edge: Edge) -> None:
        """Write `edge`'s row without committing.

        See `SqliteNodeRepository.save_without_commit` for why this exists — used by
        `SqliteResearchUnitOfWork` so a workflow-chain step's new node and its connecting
        edge commit or roll back together.
        """
        self._connection.execute(
            "INSERT INTO edges "
            "(id, workspace_id, edge_type_id, source_node_id, target_node_id, "
            " field_values_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET field_values_json = excluded.field_values_json",
            (
                edge.id,
                edge.workspace_id,
                edge.edge_type_id,
                edge.source_node_id,
                edge.target_node_id,
                json.dumps(edge.field_values),
                edge.created_at.isoformat(),
            ),
        )

    def delete(self, edge_id: EdgeId) -> None:
        with self._connection:
            self.delete_without_commit(edge_id)

    def delete_without_commit(self, edge_id: EdgeId) -> None:
        self._connection.execute("DELETE FROM edges WHERE id = ?", (edge_id,))

    def _hydrate(self, row: sqlite3.Row) -> Edge:
        return Edge(
            id=row["id"],
            workspace_id=row["workspace_id"],
            edge_type_id=row["edge_type_id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            field_values=json.loads(row["field_values_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteCanvasRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, canvas_id: CanvasId) -> Canvas | None:
        row = self._connection.execute(
            "SELECT * FROM canvases WHERE id = ?", (canvas_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[Canvas, ...]:
        rows = self._connection.execute(
            "SELECT * FROM canvases WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, canvas: Canvas) -> None:
        with self._connection:
            self.save_without_commit(canvas)

    def save_without_commit(self, canvas: Canvas) -> None:
        self._connection.execute(
            "INSERT INTO canvases (id, workspace_id, name, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            (canvas.id, canvas.workspace_id, canvas.name, canvas.created_at.isoformat()),
        )

    def _hydrate(self, row: sqlite3.Row) -> Canvas:
        return Canvas(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteCanvasPlacementRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, placement_id: CanvasPlacementId) -> CanvasPlacement | None:
        row = self._connection.execute(
            "SELECT * FROM canvas_placements WHERE id = ?", (placement_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_canvas(self, canvas_id: CanvasId) -> tuple[CanvasPlacement, ...]:
        rows = self._connection.execute(
            "SELECT * FROM canvas_placements WHERE canvas_id = ?", (canvas_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, placement: CanvasPlacement) -> None:
        with self._connection:
            self.save_without_commit(placement)

    def save_without_commit(self, placement: CanvasPlacement) -> None:
        self._connection.execute(
            "INSERT INTO canvas_placements "
            "(id, canvas_id, node_id, position_x, position_y, width, height, is_collapsed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET position_x = excluded.position_x, "
            "position_y = excluded.position_y, width = excluded.width, "
            "height = excluded.height, is_collapsed = excluded.is_collapsed",
            (
                placement.id,
                placement.canvas_id,
                placement.node_id,
                placement.position_x,
                placement.position_y,
                placement.width,
                placement.height,
                int(placement.is_collapsed),
            ),
        )

    def delete(self, placement_id: CanvasPlacementId) -> None:
        with self._connection:
            self.delete_without_commit(placement_id)

    def delete_without_commit(self, placement_id: CanvasPlacementId) -> None:
        self._connection.execute("DELETE FROM canvas_placements WHERE id = ?", (placement_id,))

    def _hydrate(self, row: sqlite3.Row) -> CanvasPlacement:
        return CanvasPlacement(
            id=row["id"],
            canvas_id=row["canvas_id"],
            node_id=row["node_id"],
            position_x=row["position_x"],
            position_y=row["position_y"],
            width=row["width"],
            height=row["height"],
            is_collapsed=bool(row["is_collapsed"]),
        )


class SqliteResourceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, resource_id: ResourceId) -> Resource | None:
        row = self._connection.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_node(self, node_id: NodeId) -> Resource | None:
        row = self._connection.execute(
            "SELECT * FROM resources WHERE node_id = ?", (node_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_canonical_identifier(
        self, workspace_id: WorkspaceId, canonical_identifier: str
    ) -> Resource | None:
        row = self._connection.execute(
            "SELECT * FROM resources WHERE workspace_id = ? AND canonical_identifier = ?",
            (workspace_id, canonical_identifier),
        ).fetchone()
        return None if row is None else self._hydrate(row)

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
        clauses = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if lifecycle_status is not None:
            clauses.append("lifecycle_status = ?")
            params.append(lifecycle_status.value)
        if repository_label is not None:
            clauses.append("repository_label = ?")
            params.append(repository_label.value)
        if last_activity_since is not None:
            clauses.append("last_activity_at >= ?")
            params.append(last_activity_since.isoformat())
        if last_activity_until is not None:
            clauses.append("last_activity_at <= ?")
            params.append(last_activity_until.isoformat())
        rows = self._connection.execute(
            f"SELECT * FROM resources WHERE {' AND '.join(clauses)}", params
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, resource: Resource) -> None:
        with self._connection:
            self.save_without_commit(resource)

    def save_without_commit(self, resource: Resource) -> None:
        """Write `resource`'s row without committing.

        See `SqliteNodeRepository.save_without_commit` for why this exists.
        """
        self._connection.execute(
            "INSERT INTO resources "
            "(id, workspace_id, node_id, kind, canonical_identifier, source_url, "
            " lifecycle_status, next_action, next_action_dismissed, open_questions_json, "
            " takeaways_json, progress_percent, review_at, last_activity_at, repository_label) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, "
            "canonical_identifier = excluded.canonical_identifier, "
            "source_url = excluded.source_url, lifecycle_status = excluded.lifecycle_status, "
            "next_action = excluded.next_action, "
            "next_action_dismissed = excluded.next_action_dismissed, "
            "open_questions_json = excluded.open_questions_json, "
            "takeaways_json = excluded.takeaways_json, "
            "progress_percent = excluded.progress_percent, review_at = excluded.review_at, "
            "last_activity_at = excluded.last_activity_at, "
            "repository_label = excluded.repository_label",
            (
                resource.id,
                resource.workspace_id,
                resource.node_id,
                resource.kind.value,
                resource.canonical_identifier,
                resource.source_url,
                resource.lifecycle_status.value,
                resource.next_action,
                int(resource.next_action_dismissed),
                json.dumps(list(resource.open_questions)),
                json.dumps(list(resource.takeaways)),
                resource.progress_percent,
                resource.review_at.isoformat() if resource.review_at is not None else None,
                resource.last_activity_at.isoformat(),
                resource.repository_label.value if resource.repository_label is not None else None,
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> Resource:
        return Resource(
            id=row["id"],
            workspace_id=row["workspace_id"],
            node_id=row["node_id"],
            kind=ResourceKind(row["kind"]),
            canonical_identifier=row["canonical_identifier"],
            source_url=row["source_url"],
            lifecycle_status=ResourceLifecycleStatus(row["lifecycle_status"]),
            next_action=row["next_action"],
            next_action_dismissed=bool(row["next_action_dismissed"]),
            open_questions=tuple(json.loads(row["open_questions_json"])),
            takeaways=tuple(json.loads(row["takeaways_json"])),
            progress_percent=row["progress_percent"],
            review_at=(
                datetime.fromisoformat(row["review_at"]) if row["review_at"] is not None else None
            ),
            last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
            repository_label=(
                RepositoryLabel(row["repository_label"])
                if row["repository_label"] is not None
                else None
            ),
        )


class SqliteSavedViewRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, saved_view_id: SavedViewId) -> SavedView | None:
        row = self._connection.execute(
            "SELECT * FROM saved_views WHERE id = ?", (saved_view_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[SavedView, ...]:
        rows = self._connection.execute(
            "SELECT * FROM saved_views WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, saved_view: SavedView) -> None:
        with self._connection:
            self.save_without_commit(saved_view)

    def save_without_commit(self, saved_view: SavedView) -> None:
        self._connection.execute(
            "INSERT INTO saved_views "
            "(id, workspace_id, name, view_kind, filter_definition_json, "
            " sort_definition_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
            "view_kind = excluded.view_kind, "
            "filter_definition_json = excluded.filter_definition_json, "
            "sort_definition_json = excluded.sort_definition_json",
            (
                saved_view.id,
                saved_view.workspace_id,
                saved_view.name,
                saved_view.view_kind.value,
                json.dumps(saved_view.filter_definition),
                json.dumps(saved_view.sort_definition),
                saved_view.created_at.isoformat(),
            ),
        )

    def delete(self, saved_view_id: SavedViewId) -> None:
        with self._connection:
            self.delete_without_commit(saved_view_id)

    def delete_without_commit(self, saved_view_id: SavedViewId) -> None:
        self._connection.execute("DELETE FROM saved_views WHERE id = ?", (saved_view_id,))

    def _hydrate(self, row: sqlite3.Row) -> SavedView:
        return SavedView(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            view_kind=ViewKind(row["view_kind"]),
            filter_definition=json.loads(row["filter_definition_json"]),
            sort_definition=json.loads(row["sort_definition_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteContextPackRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, context_pack_id: ContextPackId) -> ContextPack | None:
        row = self._connection.execute(
            "SELECT * FROM context_packs WHERE id = ?", (context_pack_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[ContextPack, ...]:
        rows = self._connection.execute(
            "SELECT * FROM context_packs WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, context_pack: ContextPack) -> None:
        with self._connection:
            self.save_without_commit(context_pack)

    def save_without_commit(self, context_pack: ContextPack) -> None:
        self._connection.execute(
            "INSERT INTO context_packs "
            "(id, workspace_id, name, node_ids_json, edge_ids_json, evidence_pointers_json, "
            " inclusion_reasons_json, object_limit, token_limit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                context_pack.id,
                context_pack.workspace_id,
                context_pack.name,
                json.dumps(list(context_pack.node_ids)),
                json.dumps(list(context_pack.edge_ids)),
                json.dumps(list(context_pack.evidence_pointers)),
                json.dumps(context_pack.inclusion_reasons),
                context_pack.object_limit,
                context_pack.token_limit,
                context_pack.created_at.isoformat(),
            ),
        )

    def delete(self, context_pack_id: ContextPackId) -> None:
        with self._connection:
            self.delete_without_commit(context_pack_id)

    def delete_without_commit(self, context_pack_id: ContextPackId) -> None:
        self._connection.execute("DELETE FROM context_packs WHERE id = ?", (context_pack_id,))

    def _hydrate(self, row: sqlite3.Row) -> ContextPack:
        return ContextPack(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            node_ids=tuple(json.loads(row["node_ids_json"])),
            edge_ids=tuple(json.loads(row["edge_ids_json"])),
            evidence_pointers=tuple(json.loads(row["evidence_pointers_json"])),
            inclusion_reasons=json.loads(row["inclusion_reasons_json"]),
            object_limit=row["object_limit"],
            token_limit=row["token_limit"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteAttachmentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, attachment_id: AttachmentId) -> Attachment | None:
        row = self._connection.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_node(self, node_id: NodeId) -> tuple[Attachment, ...]:
        rows = self._connection.execute(
            "SELECT * FROM attachments WHERE node_id = ? ORDER BY created_at", (node_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, attachment: Attachment) -> None:
        with self._connection:
            self.save_without_commit(attachment)

    def save_without_commit(self, attachment: Attachment) -> None:
        self._connection.execute(
            "INSERT INTO attachments "
            "(id, node_id, file_name, mime_type, size_bytes, checksum_sha256, "
            " storage_relative_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attachment.id,
                attachment.node_id,
                attachment.file_name,
                attachment.mime_type,
                attachment.size_bytes,
                attachment.checksum_sha256,
                attachment.storage_relative_path,
                attachment.created_at.isoformat(),
            ),
        )

    def delete(self, attachment_id: AttachmentId) -> None:
        with self._connection:
            self.delete_without_commit(attachment_id)

    def delete_without_commit(self, attachment_id: AttachmentId) -> None:
        self._connection.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> Attachment:
        return Attachment(
            id=AttachmentId(row["id"]),
            node_id=NodeId(row["node_id"]),
            file_name=row["file_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            checksum_sha256=row["checksum_sha256"],
            storage_relative_path=row["storage_relative_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteFileReferenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, file_reference_id: FileReferenceId) -> FileReference | None:
        row = self._connection.execute(
            "SELECT * FROM file_references WHERE id = ?", (file_reference_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_node(self, node_id: NodeId) -> tuple[FileReference, ...]:
        rows = self._connection.execute(
            "SELECT * FROM file_references WHERE node_id = ? ORDER BY rowid", (node_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, file_reference: FileReference) -> None:
        with self._connection:
            self.save_without_commit(file_reference)

    def save_without_commit(self, file_reference: FileReference) -> None:
        self._connection.execute(
            "INSERT INTO file_references "
            "(id, node_id, machine_name, relative_path, repository_name, absolute_path, "
            " git_ref, last_verified_at, is_missing) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET last_verified_at = excluded.last_verified_at, "
            "is_missing = excluded.is_missing",
            (
                file_reference.id,
                file_reference.node_id,
                file_reference.machine_name,
                file_reference.relative_path,
                file_reference.repository_name,
                file_reference.absolute_path,
                file_reference.git_ref,
                file_reference.last_verified_at.isoformat()
                if file_reference.last_verified_at
                else None,
                int(file_reference.is_missing),
            ),
        )

    def delete(self, file_reference_id: FileReferenceId) -> None:
        with self._connection:
            self.delete_without_commit(file_reference_id)

    def delete_without_commit(self, file_reference_id: FileReferenceId) -> None:
        self._connection.execute("DELETE FROM file_references WHERE id = ?", (file_reference_id,))

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> FileReference:
        last_verified_at = row["last_verified_at"]
        return FileReference(
            id=FileReferenceId(row["id"]),
            node_id=NodeId(row["node_id"]),
            machine_name=row["machine_name"],
            relative_path=row["relative_path"],
            repository_name=row["repository_name"],
            absolute_path=row["absolute_path"],
            git_ref=row["git_ref"],
            last_verified_at=datetime.fromisoformat(last_verified_at) if last_verified_at else None,
            is_missing=bool(row["is_missing"]),
        )


class SqlitePendingFileOperationRepository:
    """Durable journal (ST05-F01) for a managed-file operation whose completion is not yet
    certain: an upload's finalized file awaiting its row commit, or a delete's quarantined
    file awaiting either restore (row delete failed) or purge (row delete succeeded)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(
        self,
        *,
        attachment_id: AttachmentId,
        storage_relative_path: str,
        quarantine_token: str | None,
    ) -> PendingFileOperation:
        entry = PendingFileOperation(
            id=new_id(),
            attachment_id=attachment_id,
            storage_relative_path=storage_relative_path,
            quarantine_token=quarantine_token,
            created_at=datetime.now(UTC),
        )
        with self._connection:
            self._connection.execute(
                "INSERT INTO pending_file_operations "
                "(id, attachment_id, storage_relative_path, quarantine_token, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.attachment_id,
                    entry.storage_relative_path,
                    entry.quarantine_token,
                    entry.created_at.isoformat(),
                ),
            )
        return entry

    def remove(self, entry_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM pending_file_operations WHERE id = ?", (entry_id,)
            )

    def list_all(self) -> tuple[PendingFileOperation, ...]:
        rows = self._connection.execute(
            "SELECT * FROM pending_file_operations ORDER BY created_at"
        ).fetchall()
        return tuple(
            PendingFileOperation(
                id=row["id"],
                attachment_id=AttachmentId(row["attachment_id"]),
                storage_relative_path=row["storage_relative_path"],
                quarantine_token=row["quarantine_token"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )


class SqliteActivityEventRepository:
    """Audit trail writes plus MCP replay lookup (decision #14, `WORK.md`)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_by_request(
        self, workspace_id: WorkspaceId, source: str, actor_name: str, request_id: str
    ) -> ActivityEvent | None:
        row = self._connection.execute(
            "SELECT * FROM activity_events "
            "WHERE workspace_id = ? AND source = ? AND actor_name = ? AND request_id = ?",
            (workspace_id, source, actor_name, request_id),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get(self, event_id: ActivityEventId) -> ActivityEvent | None:
        row = self._connection.execute(
            "SELECT * FROM activity_events WHERE id = ?", (event_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_reverses(self, reversed_event_id: ActivityEventId) -> ActivityEvent | None:
        row = self._connection.execute(
            "SELECT * FROM activity_events WHERE reverses_event_id = ?", (reversed_event_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int,
        before_occurred_at: str | None = None,
        before_id: str | None = None,
    ) -> tuple[ActivityEvent, ...]:
        if before_occurred_at is None or before_id is None:
            rows = self._connection.execute(
                "SELECT * FROM activity_events WHERE workspace_id = ? "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM activity_events WHERE workspace_id = ? "
                "AND (occurred_at, id) < (?, ?) "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                (workspace_id, before_occurred_at, before_id, limit),
            ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def list_by_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int,
        before_occurred_at: str | None = None,
        before_id: str | None = None,
    ) -> tuple[ActivityEvent, ...]:
        if before_occurred_at is None or before_id is None:
            rows = self._connection.execute(
                "SELECT * FROM activity_events WHERE entity_type = ? AND entity_id = ? "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                (entity_type, entity_id, limit),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM activity_events WHERE entity_type = ? AND entity_id = ? "
                "AND (occurred_at, id) < (?, ?) "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                (entity_type, entity_id, before_occurred_at, before_id, limit),
            ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, event: ActivityEvent) -> None:
        with self._connection:
            self.save_without_commit(event)

    def save_without_commit(self, event: ActivityEvent) -> None:
        self._connection.execute(
            "INSERT INTO activity_events "
            "(id, workspace_id, actor_kind, actor_name, source, entity_type, entity_id, action, "
            " session_id, reason, before_state_json, after_state_json, is_undoable, occurred_at, "
            " request_id, reverses_event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.workspace_id,
                event.actor_kind.value,
                event.actor_name,
                event.source,
                event.entity_type,
                event.entity_id,
                event.action.value,
                event.session_id,
                event.reason,
                json.dumps(event.before_state) if event.before_state is not None else None,
                json.dumps(event.after_state) if event.after_state is not None else None,
                int(event.is_undoable),
                event.occurred_at.isoformat(),
                event.request_id,
                event.reverses_event_id,
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> ActivityEvent:
        return ActivityEvent(
            id=ActivityEventId(row["id"]),
            workspace_id=row["workspace_id"],
            actor_kind=ActorKind(row["actor_kind"]),
            actor_name=row["actor_name"],
            source=row["source"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            action=MutationAction(row["action"]),
            session_id=row["session_id"],
            reason=row["reason"],
            before_state=(
                json.loads(row["before_state_json"])
                if row["before_state_json"] is not None
                else None
            ),
            after_state=(
                json.loads(row["after_state_json"]) if row["after_state_json"] is not None else None
            ),
            is_undoable=bool(row["is_undoable"]),
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            request_id=row["request_id"],
            reverses_event_id=(
                ActivityEventId(row["reverses_event_id"])
                if row["reverses_event_id"] is not None
                else None
            ),
        )


class SqliteIdempotencyReceiptRepository:
    """The durable MCP replay/conflict boundary, separate from the `activity_events` audit
    trail (ST06-F01 refactor)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_by_request(
        self, workspace_id: WorkspaceId, source: str, actor_name: str, request_id: str
    ) -> IdempotencyReceipt | None:
        row = self._connection.execute(
            "SELECT * FROM idempotency_receipts "
            "WHERE workspace_id = ? AND source = ? AND actor_name = ? AND request_id = ?",
            (workspace_id, source, actor_name, request_id),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def save_without_commit(self, receipt: IdempotencyReceipt) -> None:
        self._connection.execute(
            "INSERT INTO idempotency_receipts "
            "(id, workspace_id, source, actor_name, request_id, operation, "
            " payload_fingerprint, result_payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.id,
                receipt.workspace_id,
                receipt.source,
                receipt.actor_name,
                receipt.request_id,
                receipt.operation,
                receipt.payload_fingerprint,
                json.dumps(receipt.result_payload),
                receipt.created_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> IdempotencyReceipt:
        return IdempotencyReceipt(
            id=IdempotencyReceiptId(row["id"]),
            workspace_id=row["workspace_id"],
            source=row["source"],
            actor_name=row["actor_name"],
            request_id=row["request_id"],
            operation=row["operation"],
            payload_fingerprint=row["payload_fingerprint"],
            result_payload=json.loads(row["result_payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteDiscoveryRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, discovery_run_id: DiscoveryRunId) -> DiscoveryRun | None:
        row = self._connection.execute(
            "SELECT * FROM discovery_runs WHERE id = ?", (discovery_run_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[DiscoveryRun, ...]:
        rows = self._connection.execute(
            "SELECT * FROM discovery_runs WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, discovery_run: DiscoveryRun) -> None:
        with self._connection:
            self.save_without_commit(discovery_run)

    def save_without_commit(self, discovery_run: DiscoveryRun) -> None:
        """Write `discovery_run`'s row without committing.

        See `SqliteNodeRepository.save_without_commit` for why this exists.
        """
        self._connection.execute(
            "INSERT INTO discovery_runs "
            "(id, workspace_id, agent_identity, instruction, sources_searched_json, "
            " filters_interpreted_json, candidates_json, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET candidates_json = excluded.candidates_json, "
            "completed_at = excluded.completed_at",
            (
                discovery_run.id,
                discovery_run.workspace_id,
                discovery_run.agent_identity,
                discovery_run.instruction,
                json.dumps(list(discovery_run.sources_searched)),
                json.dumps(discovery_run.filters_interpreted),
                json.dumps(
                    [candidate.model_dump(mode="json") for candidate in discovery_run.candidates]
                ),
                discovery_run.started_at.isoformat(),
                (
                    discovery_run.completed_at.isoformat()
                    if discovery_run.completed_at is not None
                    else None
                ),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> DiscoveryRun:
        return DiscoveryRun(
            id=row["id"],
            workspace_id=row["workspace_id"],
            agent_identity=row["agent_identity"],
            instruction=row["instruction"],
            sources_searched=tuple(json.loads(row["sources_searched_json"])),
            filters_interpreted=json.loads(row["filters_interpreted_json"]),
            candidates=tuple(
                DiscoveredCandidate(**candidate) for candidate in json.loads(row["candidates_json"])
            ),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
        )


class SqliteSearchIndexRepository:
    """Full-text index over node/resource text, backed by the `search_documents` FTS5 table.

    Each indexed row is keyed by `(entity_type, entity_id)`: a node's own title/body and, when
    it backs a `Resource`, that resource's identity/kind/takeaways/questions are indexed as two
    independent rows sharing the same `entity_id` (the node id), so either can be re-indexed
    without needing the other's current text.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def index_document(
        self,
        *,
        workspace_id: WorkspaceId,
        entity_type: SearchEntityType,
        entity_id: str,
        text: str,
    ) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM search_documents WHERE entity_type = ? AND entity_id = ?",
                (entity_type.value, entity_id),
            )
            self._connection.execute(
                "INSERT INTO search_documents (workspace_id, entity_type, entity_id, text) "
                "VALUES (?, ?, ?, ?)",
                (workspace_id, entity_type.value, entity_id, text),
            )

    def remove_document(self, *, entity_type: SearchEntityType, entity_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM search_documents WHERE entity_type = ? AND entity_id = ?",
                (entity_type.value, entity_id),
            )

    def search(
        self, workspace_id: WorkspaceId, query_text: str, *, limit: int = 50
    ) -> tuple[SearchHit, ...]:
        compiled_query = compile_fts5_query(query_text)
        if compiled_query is None:
            return ()
        rows = self._connection.execute(
            "SELECT entity_type, entity_id, "
            "snippet(search_documents, 3, '[', ']', '…', 12) AS snippet, "
            "bm25(search_documents) AS rank "
            "FROM search_documents "
            "WHERE search_documents MATCH ? AND workspace_id = ? "
            "ORDER BY rank LIMIT ?",
            (compiled_query, workspace_id, limit),
        ).fetchall()
        return tuple(
            SearchHit(
                entity_type=SearchEntityType(row["entity_type"]),
                entity_id=row["entity_id"],
                snippet=row["snippet"],
                rank=row["rank"],
            )
            for row in rows
        )


class SqliteResearchSettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, workspace_id: WorkspaceId) -> WorkspaceResearchSettings | None:
        row = self._connection.execute(
            "SELECT * FROM workspace_research_settings WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkspaceResearchSettings(
            workspace_id=row["workspace_id"], stale_after_days=row["stale_after_days"]
        )

    def save(self, settings: WorkspaceResearchSettings) -> None:
        with self._connection:
            self.save_without_commit(settings)

    def save_without_commit(self, settings: WorkspaceResearchSettings) -> None:
        self._connection.execute(
            "INSERT INTO workspace_research_settings (workspace_id, stale_after_days) "
            "VALUES (?, ?) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "stale_after_days = excluded.stale_after_days",
            (settings.workspace_id, settings.stale_after_days),
        )


class SqliteCollectionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, collection_id: CollectionId) -> Collection | None:
        row = self._connection.execute(
            "SELECT * FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[Collection, ...]:
        rows = self._connection.execute(
            "SELECT * FROM collections WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, collection: Collection) -> None:
        with self._connection:
            self.save_without_commit(collection)

    def save_without_commit(self, collection: Collection) -> None:
        self._connection.execute(
            "INSERT INTO collections (id, workspace_id, name, parent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name, parent_id = excluded.parent_id",
            (
                collection.id,
                collection.workspace_id,
                collection.name,
                collection.parent_id,
                collection.created_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> Collection:
        return Collection(
            id=CollectionId(row["id"]),
            workspace_id=row["workspace_id"],
            name=row["name"],
            parent_id=CollectionId(row["parent_id"]) if row["parent_id"] is not None else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteTagRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, tag_id: TagId) -> Tag | None:
        row = self._connection.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_name(self, workspace_id: WorkspaceId, name: str) -> Tag | None:
        row = self._connection.execute(
            "SELECT * FROM tags WHERE workspace_id = ? AND name = ?", (workspace_id, name)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[Tag, ...]:
        rows = self._connection.execute(
            "SELECT * FROM tags WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, tag: Tag) -> None:
        with self._connection:
            self.save_without_commit(tag)

    def save_without_commit(self, tag: Tag) -> None:
        self._connection.execute(
            "INSERT INTO tags (id, workspace_id, name, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            (tag.id, tag.workspace_id, tag.name, tag.created_at.isoformat()),
        )

    def _hydrate(self, row: sqlite3.Row) -> Tag:
        return Tag(
            id=TagId(row["id"]),
            workspace_id=row["workspace_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, document_id: DocumentId) -> Document | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(
        self, workspace_id: WorkspaceId, *, include_archived: bool = False
    ) -> tuple[Document, ...]:
        if include_archived:
            rows = self._connection.execute(
                "SELECT * FROM documents WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM documents WHERE workspace_id = ? AND is_archived = 0",
                (workspace_id,),
            ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def list_by_collection(self, collection_id: CollectionId) -> tuple[Document, ...]:
        rows = self._connection.execute(
            "SELECT * FROM documents WHERE collection_id = ?", (collection_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, document: Document) -> None:
        with self._connection:
            self.save_without_commit(document)

    def save_without_commit(self, document: Document) -> None:
        self._connection.execute(
            "INSERT INTO documents "
            "(id, workspace_id, kind, title, collection_id, is_archived, source, "
            " source_reference, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, title = excluded.title, "
            "collection_id = excluded.collection_id, is_archived = excluded.is_archived, "
            "source_reference = excluded.source_reference, updated_at = excluded.updated_at",
            (
                document.id,
                document.workspace_id,
                document.kind.value,
                document.title,
                document.collection_id,
                int(document.is_archived),
                document.source,
                document.source_reference,
                document.created_at.isoformat(),
                document.updated_at.isoformat(),
            ),
        )
        current_tag_ids = tuple(document.tag_ids)
        if current_tag_ids:
            placeholders = ",".join("?" for _ in current_tag_ids)
            self._connection.execute(
                f"DELETE FROM document_tags WHERE document_id = ? "
                f"AND tag_id NOT IN ({placeholders})",
                (document.id, *current_tag_ids),
            )
        else:
            self._connection.execute(
                "DELETE FROM document_tags WHERE document_id = ?", (document.id,)
            )
        for tag_id in current_tag_ids:
            self._connection.execute(
                "INSERT INTO document_tags (document_id, tag_id) VALUES (?, ?) "
                "ON CONFLICT (document_id, tag_id) DO NOTHING",
                (document.id, tag_id),
            )

    def _hydrate(self, row: sqlite3.Row) -> Document:
        tag_rows = self._connection.execute(
            "SELECT tag_id FROM document_tags WHERE document_id = ?", (row["id"],)
        ).fetchall()
        return Document(
            id=DocumentId(row["id"]),
            workspace_id=row["workspace_id"],
            kind=DocumentKind(row["kind"]),
            title=row["title"],
            collection_id=(
                CollectionId(row["collection_id"]) if row["collection_id"] is not None else None
            ),
            tag_ids=tuple(TagId(tag_row["tag_id"]) for tag_row in tag_rows),
            is_archived=bool(row["is_archived"]),
            source=row["source"],
            source_reference=row["source_reference"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteDocumentVersionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, document_version_id: DocumentVersionId) -> DocumentVersion | None:
        row = self._connection.execute(
            "SELECT * FROM document_versions WHERE id = ?", (document_version_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_document(self, document_id: DocumentId) -> tuple[DocumentVersion, ...]:
        rows = self._connection.execute(
            "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version_number",
            (document_id,),
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def latest_for_document(self, document_id: DocumentId) -> DocumentVersion | None:
        row = self._connection.execute(
            "SELECT * FROM document_versions WHERE document_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def save_without_commit(self, document_version: DocumentVersion) -> None:
        """Insert-only: a version is immutable once written (see `DocumentVersion`)."""
        self._connection.execute(
            "INSERT INTO document_versions "
            "(id, document_id, version_number, body_markdown, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                document_version.id,
                document_version.document_id,
                document_version.version_number,
                document_version.body_markdown,
                document_version.created_by,
                document_version.created_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            id=DocumentVersionId(row["id"]),
            document_id=DocumentId(row["document_id"]),
            version_number=row["version_number"],
            body_markdown=row["body_markdown"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteDocumentLinkRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_by_document(self, document_id: DocumentId) -> tuple[DocumentLink, ...]:
        rows = self._connection.execute(
            "SELECT * FROM document_links WHERE document_id = ?", (document_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def list_by_target(
        self, target_type: DocumentLinkTargetType, target_id: str
    ) -> tuple[DocumentLink, ...]:
        rows = self._connection.execute(
            "SELECT * FROM document_links WHERE target_type = ? AND target_id = ?",
            (target_type.value, target_id),
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, document_link: DocumentLink) -> None:
        with self._connection:
            self.save_without_commit(document_link)

    def save_without_commit(self, document_link: DocumentLink) -> None:
        self._connection.execute(
            "INSERT INTO document_links "
            "(id, document_id, target_type, target_id, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO NOTHING",
            (
                document_link.id,
                document_link.document_id,
                document_link.target_type.value,
                document_link.target_id,
                document_link.created_at.isoformat(),
            ),
        )

    def delete(self, document_link_id: DocumentLinkId) -> None:
        with self._connection:
            self.delete_without_commit(document_link_id)

    def delete_without_commit(self, document_link_id: DocumentLinkId) -> None:
        self._connection.execute("DELETE FROM document_links WHERE id = ?", (document_link_id,))

    def _hydrate(self, row: sqlite3.Row) -> DocumentLink:
        return DocumentLink(
            id=DocumentLinkId(row["id"]),
            document_id=DocumentId(row["document_id"]),
            target_type=DocumentLinkTargetType(row["target_type"]),
            target_id=row["target_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteIngestionJobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, ingestion_job_id: IngestionJobId) -> IngestionJob | None:
        row = self._connection.execute(
            "SELECT * FROM ingestion_jobs WHERE id = ?", (ingestion_job_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_source(
        self, workspace_id: WorkspaceId, source: str, source_identifier: str
    ) -> IngestionJob | None:
        row = self._connection.execute(
            "SELECT * FROM ingestion_jobs "
            "WHERE workspace_id = ? AND source = ? AND source_identifier = ?",
            (workspace_id, source, source_identifier),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_result_entity(
        self, workspace_id: WorkspaceId, *, result_entity_type: str, result_entity_id: str
    ) -> IngestionJob | None:
        """The most recent job that produced `(result_entity_type, result_entity_id)` (review
        finding S6-R02): more than one capture can legitimately resolve to the same canonical
        resource over time (a schema uniqueness constraint on source identity, not result
        entity), so this deterministically returns the latest by `created_at`, tying on `id` for
        a fully stable order rather than an unordered `fetchone()`."""
        row = self._connection.execute(
            "SELECT * FROM ingestion_jobs "
            "WHERE workspace_id = ? AND result_entity_type = ? AND result_entity_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (workspace_id, result_entity_type, result_entity_id),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[IngestionJob, ...]:
        rows = self._connection.execute(
            "SELECT * FROM ingestion_jobs WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, ingestion_job: IngestionJob) -> None:
        with self._connection:
            self.save_without_commit(ingestion_job)

    def save_without_commit(self, ingestion_job: IngestionJob) -> None:
        self._connection.execute(
            "INSERT INTO ingestion_jobs "
            "(id, workspace_id, source, source_identifier, stage, status, "
            " result_entity_type, result_entity_id, error_message, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET stage = excluded.stage, status = excluded.status, "
            "result_entity_type = excluded.result_entity_type, "
            "result_entity_id = excluded.result_entity_id, "
            "error_message = excluded.error_message, updated_at = excluded.updated_at",
            (
                ingestion_job.id,
                ingestion_job.workspace_id,
                ingestion_job.source,
                ingestion_job.source_identifier,
                ingestion_job.stage.value,
                ingestion_job.status.value,
                ingestion_job.result_entity_type,
                ingestion_job.result_entity_id,
                ingestion_job.error_message,
                ingestion_job.created_at.isoformat(),
                ingestion_job.updated_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> IngestionJob:
        return IngestionJob(
            id=IngestionJobId(row["id"]),
            workspace_id=row["workspace_id"],
            source=row["source"],
            source_identifier=row["source_identifier"],
            stage=IngestionStage(row["stage"]),
            status=IngestionJobStatus(row["status"]),
            result_entity_type=row["result_entity_type"],
            result_entity_id=row["result_entity_id"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteWorkItemRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, work_item_id: WorkItemId) -> WorkItem | None:
        row = self._connection.execute(
            "SELECT * FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_node(self, node_id: NodeId) -> WorkItem | None:
        row = self._connection.execute(
            "SELECT * FROM work_items WHERE node_id = ?", (node_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[WorkItem, ...]:
        rows = self._connection.execute(
            "SELECT * FROM work_items WHERE workspace_id = ?", (workspace_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def list_by_parent(self, parent_id: WorkItemId) -> tuple[WorkItem, ...]:
        rows = self._connection.execute(
            "SELECT * FROM work_items WHERE parent_id = ?", (parent_id,)
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save(self, work_item: WorkItem) -> None:
        with self._connection:
            self.save_without_commit(work_item)

    def save_without_commit(self, work_item: WorkItem) -> None:
        self._connection.execute(
            "INSERT INTO work_items "
            "(id, workspace_id, node_id, kind, work_type, status, parent_id, "
            " repository_node_id, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET work_type = excluded.work_type, "
            "status = excluded.status, parent_id = excluded.parent_id, "
            "repository_node_id = excluded.repository_node_id, "
            "updated_at = excluded.updated_at",
            (
                work_item.id,
                work_item.workspace_id,
                work_item.node_id,
                work_item.kind.value,
                work_item.work_type.value,
                work_item.status.value,
                work_item.parent_id,
                work_item.repository_node_id,
                work_item.source,
                work_item.created_at.isoformat(),
                work_item.updated_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=WorkItemId(row["id"]),
            workspace_id=row["workspace_id"],
            node_id=row["node_id"],
            kind=WorkItemKind(row["kind"]),
            work_type=WorkItemType(row["work_type"]),
            status=WorkItemStatus(row["status"]),
            parent_id=WorkItemId(row["parent_id"]) if row["parent_id"] is not None else None,
            repository_node_id=(
                NodeId(row["repository_node_id"]) if row["repository_node_id"] is not None else None
            ),
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteResourceEnrichmentProfileRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, profile_id: ResourceEnrichmentProfileId) -> ResourceEnrichmentProfile | None:
        row = self._connection.execute(
            "SELECT * FROM resource_enrichment_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def get_by_identifier(
        self, workspace_id: WorkspaceId, canonical_identifier: str
    ) -> ResourceEnrichmentProfile | None:
        row = self._connection.execute(
            "SELECT * FROM resource_enrichment_profiles "
            "WHERE workspace_id = ? AND canonical_identifier = ?",
            (workspace_id, canonical_identifier),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def save_without_commit(self, profile: ResourceEnrichmentProfile) -> None:
        self._connection.execute(
            "INSERT INTO resource_enrichment_profiles "
            "(id, workspace_id, canonical_identifier, resource_kind, current_version_number, "
            " current_version_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "current_version_number = excluded.current_version_number, "
            "current_version_id = excluded.current_version_id, "
            "updated_at = excluded.updated_at",
            (
                profile.id,
                profile.workspace_id,
                profile.canonical_identifier,
                profile.resource_kind.value,
                profile.current_version_number,
                profile.current_version_id,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> ResourceEnrichmentProfile:
        return ResourceEnrichmentProfile(
            id=ResourceEnrichmentProfileId(row["id"]),
            workspace_id=row["workspace_id"],
            canonical_identifier=row["canonical_identifier"],
            resource_kind=ResourceKind(row["resource_kind"]),
            current_version_number=row["current_version_number"],
            current_version_id=(
                ResourceEnrichmentProfileVersionId(row["current_version_id"])
                if row["current_version_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteResourceEnrichmentProfileVersionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(
        self, version_id: ResourceEnrichmentProfileVersionId
    ) -> ResourceEnrichmentProfileVersion | None:
        row = self._connection.execute(
            "SELECT * FROM resource_enrichment_profile_versions WHERE id = ?", (version_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_profile(
        self, profile_id: ResourceEnrichmentProfileId
    ) -> tuple[ResourceEnrichmentProfileVersion, ...]:
        rows = self._connection.execute(
            "SELECT * FROM resource_enrichment_profile_versions "
            "WHERE profile_id = ? ORDER BY version_number",
            (profile_id,),
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def create_version_without_commit(
        self, version: ResourceEnrichmentProfileVersion, *, expected_current_version_number: int
    ) -> None:
        """Insert-only: a version is immutable once written.

        Always re-reads the profile's actual current version number from the database first,
        inside the same transaction, and raises `ConcurrentEnrichmentUpdateError` immediately if
        it has already moved past what the caller expected -- a stale in-memory read is caught
        here rather than racing on the `UNIQUE (profile_id, version_number)` constraint below,
        which remains a second, defense-in-depth guard against a genuine write race.
        """
        actual_row = self._connection.execute(
            "SELECT current_version_number FROM resource_enrichment_profiles WHERE id = ?",
            (version.profile_id,),
        ).fetchone()
        actual_version_number = 0 if actual_row is None else actual_row["current_version_number"]
        if actual_version_number != expected_current_version_number:
            raise ConcurrentEnrichmentUpdateError(
                f"profile {version.profile_id} expected current version "
                f"{expected_current_version_number} but found {actual_version_number}: "
                "another enrichment write already landed"
            )
        try:
            self._connection.execute(
                "INSERT INTO resource_enrichment_profile_versions "
                "(id, profile_id, version_number, resource_kind, payload_json, tags_json, "
                " evidence_content_hashes_json, cited_evidence_json, authors_json, "
                " published_at, abstract, provider_name, model_name, confidence, "
                " created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version.id,
                    version.profile_id,
                    version.version_number,
                    version.resource_kind.value,
                    json.dumps(version.payload.model_dump(mode="json")),
                    json.dumps(list(version.tags)),
                    json.dumps(list(version.evidence_content_hashes)),
                    json.dumps(
                        [reference.model_dump(mode="json") for reference in version.cited_evidence]
                    ),
                    json.dumps(list(version.authors)),
                    version.published_at.isoformat() if version.published_at is not None else None,
                    version.abstract,
                    version.provider_name,
                    version.model_name,
                    version.confidence,
                    version.created_by,
                    version.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ConcurrentEnrichmentUpdateError(
                f"profile {version.profile_id} already has a version "
                f"{version.version_number}: another enrichment write already landed"
            ) from error

    def _hydrate(self, row: sqlite3.Row) -> ResourceEnrichmentProfileVersion:
        resource_kind = ResourceKind(row["resource_kind"])
        payload_type = payload_type_for_kind(resource_kind)
        return ResourceEnrichmentProfileVersion(
            id=ResourceEnrichmentProfileVersionId(row["id"]),
            profile_id=ResourceEnrichmentProfileId(row["profile_id"]),
            version_number=row["version_number"],
            resource_kind=resource_kind,
            payload=payload_type(**json.loads(row["payload_json"])),
            tags=tuple(json.loads(row["tags_json"])),
            evidence_content_hashes=tuple(json.loads(row["evidence_content_hashes_json"])),
            cited_evidence=tuple(
                CitedEvidenceReference(**reference)
                for reference in json.loads(row["cited_evidence_json"])
            ),
            authors=tuple(json.loads(row["authors_json"])),
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
            abstract=row["abstract"],
            provider_name=row["provider_name"],
            model_name=row["model_name"],
            confidence=row["confidence"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteRelationProposalRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, proposal_id: RelationProposalId) -> RelationProposal | None:
        row = self._connection.execute(
            "SELECT * FROM relation_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def list_by_workspace(
        self, workspace_id: WorkspaceId, *, status: RelationProposalStatus | None = None
    ) -> tuple[RelationProposal, ...]:
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM relation_proposals WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM relation_proposals WHERE workspace_id = ? AND status = ?",
                (workspace_id, status.value),
            ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def save_without_commit(self, proposal: RelationProposal) -> None:
        self._connection.execute(
            "INSERT INTO relation_proposals "
            "(id, workspace_id, profile_version_id, source_node_id, relation_kind, "
            " candidate_label, confidence, explanation, status, resolved_target_node_id, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET status = excluded.status, "
            "resolved_target_node_id = excluded.resolved_target_node_id, "
            "updated_at = excluded.updated_at",
            (
                proposal.id,
                proposal.workspace_id,
                proposal.profile_version_id,
                proposal.source_node_id,
                proposal.relation_kind.value,
                proposal.candidate_label,
                proposal.confidence,
                proposal.explanation,
                proposal.status.value,
                proposal.resolved_target_node_id,
                proposal.created_at.isoformat(),
                proposal.updated_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> RelationProposal:
        return RelationProposal(
            id=RelationProposalId(row["id"]),
            workspace_id=row["workspace_id"],
            profile_version_id=ResourceEnrichmentProfileVersionId(row["profile_version_id"]),
            source_node_id=NodeId(row["source_node_id"]),
            relation_kind=ProposedRelationKind(row["relation_kind"]),
            candidate_label=row["candidate_label"],
            confidence=row["confidence"],
            explanation=row["explanation"],
            status=RelationProposalStatus(row["status"]),
            resolved_target_node_id=(
                NodeId(row["resolved_target_node_id"])
                if row["resolved_target_node_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SqliteWorkPlanningReceiptRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_by_ingestion_job(self, ingestion_job_id: IngestionJobId) -> WorkPlanningReceipt | None:
        row = self._connection.execute(
            "SELECT * FROM work_planning_receipts WHERE ingestion_job_id = ?",
            (ingestion_job_id,),
        ).fetchone()
        return None if row is None else self._hydrate(row)

    def save_without_commit(self, receipt: WorkPlanningReceipt) -> None:
        self._connection.execute(
            "INSERT INTO work_planning_receipts "
            "(id, workspace_id, ingestion_job_id, status, epic_work_item_id, plan_document_id, "
            " plan_document_version_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.id,
                receipt.workspace_id,
                receipt.ingestion_job_id,
                receipt.status.value,
                receipt.epic_work_item_id,
                receipt.plan_document_id,
                receipt.plan_document_version_id,
                receipt.created_at.isoformat(),
            ),
        )

    def _hydrate(self, row: sqlite3.Row) -> WorkPlanningReceipt:
        return WorkPlanningReceipt(
            id=WorkPlanningReceiptId(row["id"]),
            workspace_id=row["workspace_id"],
            ingestion_job_id=IngestionJobId(row["ingestion_job_id"]),
            status=WorkPlanOutcomeStatus(row["status"]),
            epic_work_item_id=(
                WorkItemId(row["epic_work_item_id"])
                if row["epic_work_item_id"] is not None
                else None
            ),
            plan_document_id=(
                DocumentId(row["plan_document_id"]) if row["plan_document_id"] is not None else None
            ),
            plan_document_version_id=(
                DocumentVersionId(row["plan_document_version_id"])
                if row["plan_document_version_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
