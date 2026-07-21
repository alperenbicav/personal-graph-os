"""SQLite implementations of the application layer's repository protocols.

Each repository maps between the domain model and its normalized table(s). Nested schema
(field/status definitions under a node type) is reassembled on read; field/edge values
are stored as JSON since their shape is user-defined, not fixed by the relational schema.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    CanvasId,
    CanvasPlacementId,
    EdgeId,
    NodeId,
    WorkspaceId,
)
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    NodeType,
    StatusDefinition,
    Workspace,
)


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
                "INSERT INTO node_types (id, workspace_id, name, icon, color_hex) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, icon = excluded.icon, "
                "color_hex = excluded.color_hex",
                (node_type.id, workspace.id, node_type.name, node_type.icon, node_type.color_hex),
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
                "INSERT INTO edge_types (id, workspace_id, name, inverse_name, color_hex) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
                "inverse_name = excluded.inverse_name, color_hex = excluded.color_hex",
                (
                    edge_type.id,
                    workspace.id,
                    edge_type.name,
                    edge_type.inverse_name,
                    edge_type.color_hex,
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
