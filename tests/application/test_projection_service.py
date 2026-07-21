from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.schema import FieldDefinition, FieldType, NodeType, StatusDefinition
from personal_graph_os.domain.views import (
    FilterClause,
    FilterField,
    FilterOperator,
    ProjectionQuery,
    SortClause,
    SortDirection,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    task_type = NodeType(
        name="Task",
        status_definitions=(
            StatusDefinition(name="Todo"),
            StatusDefinition(name="Done"),
        ),
    )
    resource_type = NodeType(
        name="Resource",
        field_definitions=(FieldDefinition(name="notes", field_type=FieldType.TEXT),),
        system_key="resource",
    )
    workspace = new_workspace("Personal").model_copy(
        update={"node_types": (task_type, resource_type)}
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    projection_service = ProjectionService(node_repository, resource_repository)
    return (
        projection_service,
        resource_service,
        node_repository,
        workspace.id,
        task_type,
    )


def test_evaluate_table_filters_by_allowlisted_field(sqlite_connection: sqlite3.Connection) -> None:
    projection_service, _resource_service, node_repository, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    todo_status, done_status = task_type.status_definitions
    node_repository.save(
        Node(
            workspace_id=workspace_id,
            node_type_id=task_type.id,
            title="A",
            status_id=todo_status.id,
        )
    )
    node_repository.save(
        Node(
            workspace_id=workspace_id,
            node_type_id=task_type.id,
            title="B",
            status_id=done_status.id,
        )
    )

    query = ProjectionQuery(
        filters=(
            FilterClause(
                field=FilterField.STATUS_ID,
                operator=FilterOperator.EQUALS,
                value=str(done_status.id),
            ),
        )
    )
    rows = projection_service.evaluate_table(workspace_id, query)

    assert [row.node.title for row in rows] == ["B"]


def test_evaluate_table_sorts_by_created_at_descending(
    sqlite_connection: sqlite3.Connection,
) -> None:
    projection_service, _resource_service, node_repository, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    now = datetime.now(UTC)
    older = Node(
        workspace_id=workspace_id, node_type_id=task_type.id, title="Older", created_at=now
    )
    newer = Node(
        workspace_id=workspace_id,
        node_type_id=task_type.id,
        title="Newer",
        created_at=now + timedelta(hours=1),
    )
    node_repository.save(older)
    node_repository.save(newer)

    query = ProjectionQuery(
        sort=(SortClause(field=FilterField.CREATED_AT, direction=SortDirection.DESC),)
    )
    rows = projection_service.evaluate_table(workspace_id, query)

    assert [row.node.title for row in rows] == ["Newer", "Older"]


def test_evaluate_table_excludes_archived_by_default(sqlite_connection: sqlite3.Connection) -> None:
    projection_service, _resource_service, node_repository, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    archived = Node(
        workspace_id=workspace_id, node_type_id=task_type.id, title="Gone", is_archived=True
    )
    node_repository.save(archived)

    assert projection_service.evaluate_table(workspace_id, ProjectionQuery()) == ()
    included = projection_service.evaluate_table(
        workspace_id, ProjectionQuery(include_archived=True)
    )
    assert [row.node.title for row in included] == ["Gone"]


def test_evaluate_kanban_groups_by_resource_lifecycle_status(
    sqlite_connection: sqlite3.Connection,
) -> None:
    projection_service, resource_service, _node_repository, workspace_id, _task_type = _fixture(
        sqlite_connection
    )
    resource_a, _ = resource_service.create_or_reuse(
        workspace_id, "Paper A", "https://arxiv.org/abs/2401.00001", kind=ResourceKind.PAPER
    )
    resource_b, _ = resource_service.create_or_reuse(
        workspace_id, "Paper B", "https://arxiv.org/abs/2401.00002", kind=ResourceKind.PAPER
    )
    resource_service.update(resource_b.id, lifecycle_status=ResourceLifecycleStatus.READING)

    columns = projection_service.evaluate_kanban(
        workspace_id, ProjectionQuery(), FilterField.RESOURCE_LIFECYCLE_STATUS
    )

    inbox_resources = [item.resource for item in columns["inbox"]]
    reading_resources = [item.resource for item in columns["reading"]]
    assert all(resource is not None for resource in (*inbox_resources, *reading_resources))
    assert [resource.id for resource in inbox_resources if resource is not None] == [resource_a.id]
    assert [resource.id for resource in reading_resources if resource is not None] == [
        resource_b.id
    ]


def test_evaluate_timeline_falls_back_to_created_at_when_date_field_unset(
    sqlite_connection: sqlite3.Connection,
) -> None:
    projection_service, resource_service, _node_repository, workspace_id, _task_type = _fixture(
        sqlite_connection
    )
    resource, _ = resource_service.create_or_reuse(
        workspace_id, "Paper", "https://arxiv.org/abs/2401.00003", kind=ResourceKind.PAPER
    )
    assert resource.review_at is None

    rows = projection_service.evaluate_timeline(
        workspace_id, ProjectionQuery(), FilterField.REVIEW_AT
    )

    row_resources = [row.resource for row in rows]
    assert [r.id for r in row_resources if r is not None] == [resource.id]
