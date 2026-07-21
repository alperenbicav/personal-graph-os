from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.services import (
    EdgeService,
    EdgeTypeNotFoundError,
    FieldDefinitionNotFoundError,
    NodeService,
    NodeTypeNotFoundError,
    SchemaEditConflictError,
    SchemaService,
    StatusDefinitionNotFoundError,
    WorkspaceNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    EdgeTypeId,
    FieldDefinitionId,
    NodeTypeId,
    StatusDefinitionId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.schema import EdgeType, FieldType, NodeType, StatusDefinition
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)


def _services(sqlite_connection: sqlite3.Connection):
    task_type = NodeType(
        name="Task",
        status_definitions=(StatusDefinition(name="Open"), StatusDefinition(name="Done")),
    )
    edge_type = EdgeType(name="relates_to")
    workspace = new_workspace("Personal").model_copy(
        update={"node_types": (task_type,), "edge_types": (edge_type,)}
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    edge_repository = SqliteEdgeRepository(sqlite_connection)

    node_service = NodeService(workspace_repository, node_repository)
    edge_service = EdgeService(workspace_repository, node_repository, edge_repository)
    schema_service = SchemaService(workspace_repository, node_repository, edge_repository)
    return (
        schema_service,
        node_service,
        edge_service,
        workspace_repository,
        task_type,
        edge_type,
        workspace.id,
    )


def test_create_node_type_adds_it_to_the_workspace(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, _task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    created = schema_service.create_node_type(workspace_id, "Project", icon="folder")

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(created.id)
    assert reloaded_type is not None
    assert reloaded_type.icon == "folder"


def test_create_node_type_rejects_duplicate_name(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(InvariantViolationError):
        schema_service.create_node_type(workspace_id, task_type.name)


def test_create_node_type_rejects_unknown_workspace(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, *_rest = _services(sqlite_connection)

    with pytest.raises(WorkspaceNotFoundError):
        schema_service.create_node_type(WorkspaceId(new_id()), "Anything")


def test_update_node_type_renames_it(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    updated = schema_service.update_node_type(workspace_id, task_type.id, name="Todo", icon="check")

    assert updated.name == "Todo"
    assert updated.icon == "check"
    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(task_type.id)
    assert reloaded_type is not None
    assert reloaded_type.name == "Todo"


def test_update_node_type_rejects_unknown_node_type(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, *_rest, workspace_id = _services(sqlite_connection)

    with pytest.raises(NodeTypeNotFoundError):
        schema_service.update_node_type(workspace_id, NodeTypeId(new_id()), name="X")


def test_add_field_definition_extends_the_node_type(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "priority", FieldType.TEXT
    )

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(task_type.id)
    assert reloaded_type is not None
    assert reloaded_type.field_by_id(field.id) is not None


def test_add_field_definition_rejects_duplicate_name_on_the_same_node_type(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    schema_service.add_field_definition(workspace_id, task_type.id, "priority", FieldType.TEXT)

    with pytest.raises(InvariantViolationError):
        schema_service.add_field_definition(
            workspace_id, task_type.id, "priority", FieldType.NUMBER
        )


def test_add_field_definition_select_without_options_is_rejected(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(InvariantViolationError):
        schema_service.add_field_definition(
            workspace_id, task_type.id, "category", FieldType.SELECT
        )


def test_add_required_field_succeeds_even_when_existing_nodes_have_not_set_it(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A required field must never retroactively invalidate a node that simply never set
    it (product principle 6: capture requires only a title, forever, not just at capture
    time) — only an existing *explicit* `None`/incompatible value blocks the edit."""
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    node_service.capture(workspace_id, task_type.id, "Existing task")

    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "owner", FieldType.TEXT, is_required=True
    )
    assert field.is_required is True


def test_add_required_field_succeeds_when_no_existing_node_would_be_invalidated(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "owner", FieldType.TEXT, is_required=True
    )
    assert field.is_required is True


def test_update_field_definition_renames_and_changes_type(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "priority", FieldType.TEXT
    )

    updated = schema_service.update_field_definition(
        workspace_id, task_type.id, field.id, name="urgency", field_type=FieldType.NUMBER
    )

    assert updated.name == "urgency"
    assert updated.field_type == FieldType.NUMBER


def test_update_field_definition_narrowing_type_rejected_if_existing_value_is_incompatible(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "priority", FieldType.TEXT
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    node_service.update(node.id, field_values={field.id: "high"})

    with pytest.raises(SchemaEditConflictError):
        schema_service.update_field_definition(
            workspace_id, task_type.id, field.id, field_type=FieldType.NUMBER
        )


def test_converting_a_field_to_object_reference_rejects_a_dangling_existing_value(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for the reviewer's final probe: `SchemaService` must run the same
    repository-backed object-reference check `NodeService` runs on direct writes, or
    converting an existing text field to `object_reference` silently persists a value that
    was never a real node id."""
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "repository", FieldType.TEXT
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    node_service.update(node.id, field_values={field.id: "not-a-node-id"})

    with pytest.raises(SchemaEditConflictError):
        schema_service.update_field_definition(
            workspace_id, task_type.id, field.id, field_type=FieldType.OBJECT_REFERENCE
        )


def test_converting_a_field_to_object_reference_succeeds_when_the_existing_value_is_a_real_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "repository", FieldType.TEXT
    )
    target = node_service.capture(workspace_id, task_type.id, "Target")
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    node_service.update(node.id, field_values={field.id: target.id})

    updated_field = schema_service.update_field_definition(
        workspace_id, task_type.id, field.id, field_type=FieldType.OBJECT_REFERENCE
    )

    assert updated_field.field_type == FieldType.OBJECT_REFERENCE


def test_update_field_definition_rejects_unknown_field(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(FieldDefinitionNotFoundError):
        schema_service.update_field_definition(
            workspace_id, task_type.id, FieldDefinitionId(new_id()), name="x"
        )


def test_remove_field_definition_removes_an_unused_field(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, workspace_repository, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "priority", FieldType.TEXT
    )

    schema_service.remove_field_definition(workspace_id, task_type.id, field.id)

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(task_type.id)
    assert reloaded_type is not None
    assert reloaded_type.field_by_id(field.id) is None


def test_remove_field_definition_rejects_when_a_node_still_has_a_value(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    field = schema_service.add_field_definition(
        workspace_id, task_type.id, "priority", FieldType.TEXT
    )
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    node_service.update(node.id, field_values={field.id: "high"})

    with pytest.raises(SchemaEditConflictError):
        schema_service.remove_field_definition(workspace_id, task_type.id, field.id)


def test_add_status_definition_extends_the_node_type(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    status = schema_service.add_status_definition(workspace_id, task_type.id, "Blocked")

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(task_type.id)
    assert reloaded_type is not None
    assert reloaded_type.status_by_id(status.id) is not None


def test_update_status_definition_renames_it(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    open_status = task_type.status_definitions[0]

    updated = schema_service.update_status_definition(
        workspace_id, task_type.id, open_status.id, name="In Progress", is_terminal=False
    )

    assert updated.name == "In Progress"


def test_update_status_definition_rejects_unknown_status(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(StatusDefinitionNotFoundError):
        schema_service.update_status_definition(
            workspace_id, task_type.id, StatusDefinitionId(new_id()), name="x"
        )


def test_remove_status_definition_removes_an_unused_status(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, workspace_repository, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    done_status = task_type.status_definitions[1]

    schema_service.remove_status_definition(workspace_id, task_type.id, done_status.id)

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_type = reloaded.node_type_by_id(task_type.id)
    assert reloaded_type is not None
    assert reloaded_type.status_by_id(done_status.id) is None


def test_remove_status_definition_rejects_when_a_node_currently_holds_it(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, node_service, _e, _repo, task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    open_status = task_type.status_definitions[0]
    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    node_service.update(node.id, status_id=open_status.id)

    with pytest.raises(SchemaEditConflictError):
        schema_service.remove_status_definition(workspace_id, task_type.id, open_status.id)


def test_create_edge_type_adds_it_to_the_workspace(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, _task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )

    created = schema_service.create_edge_type(workspace_id, "supports", inverse_name="supported_by")

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_edge_type = reloaded.edge_type_by_id(created.id)
    assert reloaded_edge_type is not None
    assert reloaded_edge_type.inverse_name == "supported_by"


def test_create_edge_type_rejects_duplicate_name(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, _repo, _task_type, edge_type, workspace_id = _services(
        sqlite_connection
    )

    with pytest.raises(InvariantViolationError):
        schema_service.create_edge_type(workspace_id, edge_type.name)


def test_update_edge_type_can_clear_inverse_name(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, _n, _e, workspace_repository, _task_type, _edge_type, workspace_id = _services(
        sqlite_connection
    )
    created = schema_service.create_edge_type(workspace_id, "supports", inverse_name="supported_by")

    updated = schema_service.update_edge_type(workspace_id, created.id, clear_inverse_name=True)

    assert updated.inverse_name is None
    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    reloaded_edge_type = reloaded.edge_type_by_id(created.id)
    assert reloaded_edge_type is not None
    assert reloaded_edge_type.inverse_name is None


def test_update_edge_type_rejects_unknown_edge_type(sqlite_connection: sqlite3.Connection) -> None:
    schema_service, *_rest, workspace_id = _services(sqlite_connection)

    with pytest.raises(EdgeTypeNotFoundError):
        schema_service.update_edge_type(workspace_id, EdgeTypeId(new_id()), name="x")


def test_remove_edge_type_removes_an_unused_edge_type(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, _n, _e, workspace_repository, _task_type, edge_type, workspace_id = _services(
        sqlite_connection
    )

    schema_service.remove_edge_type(workspace_id, edge_type.id)

    reloaded = workspace_repository.get(workspace_id)
    assert reloaded is not None
    assert reloaded.edge_type_by_id(edge_type.id) is None


def test_remove_edge_type_rejects_when_an_edge_still_uses_it(
    sqlite_connection: sqlite3.Connection,
) -> None:
    schema_service, node_service, edge_service, _repo, task_type, edge_type, workspace_id = (
        _services(sqlite_connection)
    )
    source = node_service.capture(workspace_id, task_type.id, "Source")
    target = node_service.capture(workspace_id, task_type.id, "Target")
    edge_service.connect(workspace_id, edge_type.id, source.id, target.id)

    with pytest.raises(SchemaEditConflictError):
        schema_service.remove_edge_type(workspace_id, edge_type.id)
