from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.services import (
    CanvasNotFoundError,
    CanvasService,
    EdgeService,
    NodeNotFoundError,
    NodeService,
    PlacementNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.errors import InvariantViolationError, UnknownSchemaReferenceError
from personal_graph_os.domain.identifiers import (
    CanvasId,
    CanvasPlacementId,
    EdgeTypeId,
    NodeId,
    new_id,
)
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteCanvasPlacementRepository,
    SqliteCanvasRepository,
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)


def _services(sqlite_connection: sqlite3.Connection):
    task_type = NodeType(name="Task")
    edge_type = EdgeType(name="relates_to")
    workspace = new_workspace("Personal").model_copy(
        update={"node_types": (task_type,), "edge_types": (edge_type,)}
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)

    node_repository = SqliteNodeRepository(sqlite_connection)
    node_service = NodeService(workspace_repository, node_repository)
    edge_service = EdgeService(
        workspace_repository, node_repository, SqliteEdgeRepository(sqlite_connection)
    )
    canvas_service = CanvasService(
        workspace_repository,
        node_repository,
        SqliteCanvasRepository(sqlite_connection),
        SqliteCanvasPlacementRepository(sqlite_connection),
    )
    return node_service, edge_service, canvas_service, task_type, edge_type, workspace.id


def test_connect_creates_a_typed_edge_between_two_nodes(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, edge_service, _canvas_service, task_type, edge_type, workspace_id = _services(
        sqlite_connection
    )
    source = node_service.capture(workspace_id, task_type.id, "Source")
    target = node_service.capture(workspace_id, task_type.id, "Target")

    edge = edge_service.connect(workspace_id, edge_type.id, source.id, target.id)

    assert edge.source_node_id == source.id
    assert edge.target_node_id == target.id


def test_connect_rejects_unknown_edge_type(sqlite_connection: sqlite3.Connection) -> None:
    node_service, edge_service, _canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    source = node_service.capture(workspace_id, task_type.id, "Source")
    target = node_service.capture(workspace_id, task_type.id, "Target")

    with pytest.raises(UnknownSchemaReferenceError):
        edge_service.connect(workspace_id, EdgeTypeId(new_id()), source.id, target.id)


def test_connect_rejects_node_from_another_workspace(sqlite_connection: sqlite3.Connection) -> None:
    node_service, edge_service, _canvas_service, task_type, edge_type, workspace_id = _services(
        sqlite_connection
    )
    source = node_service.capture(workspace_id, task_type.id, "Source")

    with pytest.raises(NodeNotFoundError):
        edge_service.connect(workspace_id, edge_type.id, source.id, NodeId(new_id()))


def test_place_node_creates_a_reusable_canvas_placement(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _edge_service, canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    canvas = canvas_service.create_canvas(workspace_id, "Main")

    placement = canvas_service.place_node(canvas.id, node.id, position_x=1.0, position_y=2.0)

    assert placement.node_id == node.id
    assert placement.canvas_id == canvas.id


def test_place_node_rejects_unknown_canvas(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _edge_service, canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")

    with pytest.raises(CanvasNotFoundError):
        canvas_service.place_node(CanvasId(new_id()), node.id, position_x=0.0, position_y=0.0)


def test_update_placement_moves_a_node_and_preserves_other_fields(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _edge_service, canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    canvas = canvas_service.create_canvas(workspace_id, "Main")
    placement = canvas_service.place_node(canvas.id, node.id, position_x=1.0, position_y=2.0)

    moved = canvas_service.update_placement(placement.id, position_x=10.0, position_y=20.0)

    assert moved.position_x == 10.0
    assert moved.position_y == 20.0
    assert moved.width == placement.width
    assert moved.height == placement.height
    assert moved.is_collapsed == placement.is_collapsed


def test_update_placement_can_collapse_and_resize(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _edge_service, canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    canvas = canvas_service.create_canvas(workspace_id, "Main")
    placement = canvas_service.place_node(canvas.id, node.id, position_x=0.0, position_y=0.0)

    collapsed = canvas_service.update_placement(placement.id, is_collapsed=True, width=80.0)

    assert collapsed.is_collapsed is True
    assert collapsed.width == 80.0
    assert collapsed.position_x == placement.position_x


def test_update_placement_rejects_non_positive_size(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _edge_service, canvas_service, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    canvas = canvas_service.create_canvas(workspace_id, "Main")
    placement = canvas_service.place_node(canvas.id, node.id, position_x=0.0, position_y=0.0)

    with pytest.raises(InvariantViolationError):
        canvas_service.update_placement(placement.id, width=0.0)


def test_update_placement_rejects_unknown_placement(sqlite_connection: sqlite3.Connection) -> None:
    _node_service, _edge_service, canvas_service, _task_type, _edge_type, _workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(PlacementNotFoundError):
        canvas_service.update_placement(CanvasPlacementId(new_id()), position_x=1.0)
