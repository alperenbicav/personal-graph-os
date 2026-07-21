"""SQLite implementations of the application layer's repository protocols.

Each repository maps between the domain model and its normalized table(s). Nested schema
(field/status definitions under a node type) is reassembled on read; field/edge values
are stored as JSON since their shape is user-defined, not fixed by the relational schema.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from personal_graph_os.domain.activity import DiscoveredCandidate, DiscoveryRun
from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    CanvasId,
    CanvasPlacementId,
    DiscoveryRunId,
    EdgeId,
    NodeId,
    ResourceId,
    SavedViewId,
    WorkspaceId,
)
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import Resource, ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    NodeType,
    StatusDefinition,
    Workspace,
)
from personal_graph_os.domain.search import SearchEntityType, SearchHit, compile_fts5_query
from personal_graph_os.domain.views import SavedView, ViewKind


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

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        rows = self._connection.execute(
            "SELECT * FROM resources WHERE workspace_id = ?", (workspace_id,)
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
            " takeaways_json, review_at, last_activity_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, "
            "canonical_identifier = excluded.canonical_identifier, "
            "source_url = excluded.source_url, lifecycle_status = excluded.lifecycle_status, "
            "next_action = excluded.next_action, "
            "next_action_dismissed = excluded.next_action_dismissed, "
            "open_questions_json = excluded.open_questions_json, "
            "takeaways_json = excluded.takeaways_json, review_at = excluded.review_at, "
            "last_activity_at = excluded.last_activity_at",
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
                resource.review_at.isoformat() if resource.review_at is not None else None,
                resource.last_activity_at.isoformat(),
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
            review_at=(
                datetime.fromisoformat(row["review_at"]) if row["review_at"] is not None else None
            ),
            last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
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
            self._connection.execute(
                "INSERT INTO workspace_research_settings (workspace_id, stale_after_days) "
                "VALUES (?, ?) "
                "ON CONFLICT (workspace_id) DO UPDATE SET "
                "stale_after_days = excluded.stale_after_days",
                (settings.workspace_id, settings.stale_after_days),
            )
