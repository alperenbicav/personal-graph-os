from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.application.services import NodeNotFoundError, NodeService, new_workspace
from personal_graph_os.domain.errors import (
    FieldValueTypeError,
    InvariantViolationError,
    UnknownSchemaReferenceError,
)
from personal_graph_os.domain.identifiers import NodeId, NodeTypeId, WorkspaceId, new_id
from personal_graph_os.domain.schema import FieldDefinition, FieldType, NodeType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)


def _node_service(
    sqlite_connection: sqlite3.Connection,
) -> tuple[NodeService, NodeType, WorkspaceId]:
    task_type = NodeType(
        name="Task",
        field_definitions=(
            FieldDefinition(
                name="priority", field_type=FieldType.SELECT, select_options=("low", "high")
            ),
            FieldDefinition(name="due_date", field_type=FieldType.DATE),
        ),
    )
    workspace = new_workspace("Personal").model_copy(update={"node_types": (task_type,)})
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_service = NodeService(workspace_repository, SqliteNodeRepository(sqlite_connection))
    return node_service, task_type, workspace.id


def test_capture_creates_a_node_from_only_a_title(sqlite_connection: sqlite3.Connection) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")
    assert node.title == "Write report"
    assert node.field_values == {}


def test_capture_rejects_unknown_node_type(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _task_type, workspace_id = _node_service(sqlite_connection)
    with pytest.raises(UnknownSchemaReferenceError):
        node_service.capture(workspace_id, NodeTypeId(new_id()), "Write report")


def test_update_merges_field_values_and_validates_them(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")
    priority_field = task_type.field_definitions[0]

    updated = node_service.update(node.id, field_values={priority_field.id: "high"})
    assert updated.field_values[priority_field.id] == "high"

    with pytest.raises(FieldValueTypeError):
        node_service.update(node.id, field_values={priority_field.id: "medium"})


def test_update_unknown_node_raises_not_found(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _task_type, _workspace_id = _node_service(sqlite_connection)
    with pytest.raises(NodeNotFoundError):
        node_service.update(NodeId(new_id()), title="x")


def test_archive_marks_node_archived(sqlite_connection: sqlite3.Connection) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")
    archived = node_service.archive(node.id)
    assert archived.is_archived is True


def test_update_rejects_blank_title_and_leaves_the_stored_row_unchanged(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")

    with pytest.raises(InvariantViolationError):
        node_service.update(node.id, title="   ")

    node_repository = SqliteNodeRepository(sqlite_connection)
    stored = node_repository.get(node.id)
    assert stored is not None
    assert stored.title == "Write report"


_A_KNOWN_STALE_TIMESTAMP = datetime(2020, 1, 1, tzinfo=UTC)


def test_update_advances_updated_at(sqlite_connection: sqlite3.Connection) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")

    # Force a known-stale `updated_at` directly through the repository (bypassing the
    # service, which always stamps "now") so the "advances" assertion is deterministic
    # rather than dependent on real-clock resolution between two service calls.
    node_repository = SqliteNodeRepository(sqlite_connection)
    stale_node = node.model_copy(update={"updated_at": _A_KNOWN_STALE_TIMESTAMP})
    node_repository.save(stale_node)

    updated = node_service.update(node.id, body="More detail")

    assert updated.updated_at > _A_KNOWN_STALE_TIMESTAMP
    assert updated.created_at == node.created_at


def test_archive_advances_updated_at(sqlite_connection: sqlite3.Connection) -> None:
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")

    node_repository = SqliteNodeRepository(sqlite_connection)
    stale_node = node.model_copy(update={"updated_at": _A_KNOWN_STALE_TIMESTAMP})
    node_repository.save(stale_node)

    archived = node_service.archive(node.id)

    assert archived.updated_at > _A_KNOWN_STALE_TIMESTAMP


def _node_service_with_object_reference_field(
    sqlite_connection: sqlite3.Connection,
) -> tuple[NodeService, NodeType, WorkspaceId]:
    repository_field = FieldDefinition(name="repository", field_type=FieldType.OBJECT_REFERENCE)
    task_type = NodeType(name="Task", field_definitions=(repository_field,))
    workspace = new_workspace("Personal").model_copy(update={"node_types": (task_type,)})
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_service = NodeService(workspace_repository, SqliteNodeRepository(sqlite_connection))
    return node_service, task_type, workspace.id


def test_capture_accepts_an_object_reference_to_a_real_node_in_the_same_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, task_type, workspace_id = _node_service_with_object_reference_field(
        sqlite_connection
    )
    repository_field = task_type.field_definitions[0]
    target = node_service.capture(workspace_id, task_type.id, "Target repo")

    node = node_service.capture(workspace_id, task_type.id, "Task 1")
    updated = node_service.update(node.id, field_values={repository_field.id: target.id})

    assert updated.field_values[repository_field.id] == target.id


def test_update_rejects_an_object_reference_to_a_nonexistent_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, task_type, workspace_id = _node_service_with_object_reference_field(
        sqlite_connection
    )
    repository_field = task_type.field_definitions[0]
    node = node_service.capture(workspace_id, task_type.id, "Task 1")

    with pytest.raises(UnknownSchemaReferenceError):
        node_service.update(node.id, field_values={repository_field.id: "not-a-node-id"})


def test_update_rejects_an_object_reference_to_a_node_in_another_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, task_type, workspace_id = _node_service_with_object_reference_field(
        sqlite_connection
    )
    repository_field = task_type.field_definitions[0]
    node = node_service.capture(workspace_id, task_type.id, "Task 1")

    other_task_type = NodeType(name="Task")
    other_workspace = new_workspace("Other").model_copy(update={"node_types": (other_task_type,)})
    SqliteWorkspaceRepository(sqlite_connection).save(other_workspace)
    other_node = node_service.capture(
        other_workspace.id, other_task_type.id, "Other workspace's node"
    )

    with pytest.raises(UnknownSchemaReferenceError):
        node_service.update(node.id, field_values={repository_field.id: other_node.id})


def test_update_persists_a_date_field_value(sqlite_connection: sqlite3.Connection) -> None:
    """Regression for ST-01 review finding: updating a DATE field must persist through
    SQLite (JSON) without raising, matching the seeded Task type's `due_date` field."""
    node_service, task_type, workspace_id = _node_service(sqlite_connection)
    node = node_service.capture(workspace_id, task_type.id, "Write report")
    due_date_field = task_type.field_definitions[1]

    updated = node_service.update(node.id, field_values={due_date_field.id: "2026-08-01"})

    assert updated.field_values[due_date_field.id] == "2026-08-01"
    reloaded = SqliteNodeRepository(sqlite_connection).get(node.id)
    assert reloaded is not None
    assert reloaded.field_values[due_date_field.id] == "2026-08-01"
