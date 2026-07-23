"""NodeService/ResourceService keep `search_documents` current as the sole mutation paths;
`SearchService` resolves hits back to live Node/Resource state (archived exclusion, in
particular, is always evaluated live rather than baked into the index)."""

from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.bootstrap import backfill_search_index
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.services import (
    NodeService,
    ResourceService,
    SchemaEditConflictError,
    SchemaService,
    new_workspace,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.schema import FieldDefinition, FieldType, NodeType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    notes_field = FieldDefinition(name="Notes", field_type=FieldType.TEXT)
    priority_field = FieldDefinition(
        name="Priority", field_type=FieldType.SELECT, select_options=("low", "high")
    )
    task_type = NodeType(name="Task", field_definitions=(notes_field, priority_field))
    resource_type = NodeType(name="Resource", system_key="resource")
    workspace = new_workspace("Personal").model_copy(
        update={"node_types": (task_type, resource_type)}
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)

    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)

    node_service = NodeService(
        workspace_repository,
        node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index,
    )
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index,
    )
    search_service = SearchService(node_repository, resource_repository, search_index)
    return node_service, resource_service, search_service, workspace.id, task_type


def test_capture_indexes_title_and_search_finds_it(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node_service.capture(workspace_id, task_type.id, "Attention Is All You Need")

    results = search_service.search(workspace_id, "attention")

    assert [result.node.title for result in results] == ["Attention Is All You Need"]


def test_update_reindexes_so_search_reflects_the_new_title(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Old title")
    node_service.update(node.id, title="Renamed title")

    assert search_service.search(workspace_id, "old") == ()
    assert [r.node.title for r in search_service.search(workspace_id, "renamed")] == [
        "Renamed title"
    ]


def test_archived_nodes_are_excluded_from_search_by_default(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node = node_service.capture(workspace_id, task_type.id, "Findable thing")
    node_service.archive(node.id)

    assert search_service.search(workspace_id, "findable") == ()
    included = search_service.search(workspace_id, "findable", include_archived=True)
    assert [r.node.title for r in included] == ["Findable thing"]


def test_search_matches_resource_identity_and_returns_the_backing_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    _node_service, resource_service, search_service, workspace_id, _task_type = _fixture(
        sqlite_connection
    )
    resource, _ = resource_service.create_or_reuse(
        workspace_id,
        "A great paper",
        "https://arxiv.org/abs/2401.00001",
        kind=ResourceKind.PAPER,
    )

    results = search_service.search(workspace_id, "arxiv")

    assert len(results) == 1
    assert results[0].node.id == resource.node_id
    assert results[0].resource is not None
    assert results[0].resource.id == resource.id


def test_search_query_with_special_characters_does_not_raise_and_finds_no_false_positive(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node_service.capture(workspace_id, task_type.id, "Ordinary title")

    results = search_service.search(workspace_id, 'title:foo OR NOT ("bar")')

    assert results == ()


def test_search_finds_a_custom_text_field_value(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "ultrauniqueprobe"})

    results = search_service.search(workspace_id, "ultrauniqueprobe")

    assert [r.node.id for r in results] == [node.id]


def test_search_ignores_a_non_text_field_value(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    priority_field = next(f for f in task_type.field_definitions if f.name == "Priority")
    node_service.capture(workspace_id, task_type.id, "A task")
    node = node_service.capture(workspace_id, task_type.id, "Another task")
    node_service.update(node.id, field_values={priority_field.id: "high"})

    assert search_service.search(workspace_id, "high") == ()


def test_removing_a_custom_text_field_value_stops_it_from_matching(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "findableword"})
    node_service.update(node.id, field_values={notes_field.id: ""})

    assert search_service.search(workspace_id, "findableword") == ()


def test_backfill_reindexes_a_custom_text_field_value_after_a_restart(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Simulates a process restart: the search index is dropped, then rebuilt purely from
    already-persisted rows via `backfill_search_index` (as `create_app()` does on startup)."""
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "restartprobe"})

    sqlite_connection.execute("DELETE FROM search_documents")

    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)
    workspace = SqliteWorkspaceRepository(sqlite_connection).get(workspace_id)
    assert workspace is not None
    backfill_search_index(node_repository, resource_repository, search_index, workspace)

    results = search_service.search(workspace_id, "restartprobe")
    assert [r.node.id for r in results] == [node.id]


def test_search_is_workspace_scoped(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node_service.capture(workspace_id, task_type.id, "Shared keyword")

    other_workspace = new_workspace("Other")
    SqliteWorkspaceRepository(sqlite_connection).save(other_workspace)

    assert search_service.search(other_workspace.id, "shared") == ()
    assert len(search_service.search(workspace_id, "shared")) == 1


def _schema_service(sqlite_connection: sqlite3.Connection) -> SchemaService:
    return SchemaService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteNodeRepository(sqlite_connection),
        SqliteEdgeRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=SqliteSearchIndexRepository(sqlite_connection),
    )


def test_schema_change_from_text_to_non_text_stops_the_value_from_matching(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "schemaprobeone"})
    assert [r.node.id for r in search_service.search(workspace_id, "schemaprobeone")] == [node.id]

    schema_service = _schema_service(sqlite_connection)
    schema_service.update_field_definition(
        workspace_id, task_type.id, notes_field.id, field_type=FieldType.FILE_PATH
    )

    assert search_service.search(workspace_id, "schemaprobeone") == ()


def test_schema_change_from_non_text_to_text_makes_the_value_searchable(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    priority_field = next(f for f in task_type.field_definitions if f.name == "Priority")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={priority_field.id: "high"})
    assert search_service.search(workspace_id, "high") == ()

    schema_service = _schema_service(sqlite_connection)
    schema_service.update_field_definition(
        workspace_id, task_type.id, priority_field.id, field_type=FieldType.TEXT
    )

    assert [r.node.id for r in search_service.search(workspace_id, "high")] == [node.id]


def test_removing_an_unused_text_field_reindexes_affected_nodes_without_error(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A field can only be removed while no node currently holds a value for it (existing
    invariant), so removal's own regression is that affected nodes still reindex cleanly
    against the post-removal schema rather than erroring or leaving stale index rows."""
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A removable-field task")

    schema_service = _schema_service(sqlite_connection)
    schema_service.remove_field_definition(workspace_id, task_type.id, notes_field.id)

    assert [r.node.id for r in search_service.search(workspace_id, "removable-field")] == [node.id]


def test_a_rejected_schema_change_does_not_alter_the_search_index(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "schemaprobethree"})

    schema_service = _schema_service(sqlite_connection)
    with pytest.raises(SchemaEditConflictError):
        schema_service.update_field_definition(
            workspace_id, task_type.id, notes_field.id, field_type=FieldType.NUMBER
        )

    assert [r.node.id for r in search_service.search(workspace_id, "schemaprobethree")] == [node.id]


def test_schema_change_reindex_survives_a_restart(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node_service, resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    notes_field = next(f for f in task_type.field_definitions if f.name == "Notes")
    node = node_service.capture(workspace_id, task_type.id, "A task")
    node_service.update(node.id, field_values={notes_field.id: "schemaprobefour"})

    schema_service = _schema_service(sqlite_connection)
    schema_service.update_field_definition(
        workspace_id, task_type.id, notes_field.id, field_type=FieldType.FILE_PATH
    )

    # Simulate a process restart: drop the index, then rebuild purely from persisted rows
    # against the now-current (post-schema-change) schema.
    sqlite_connection.execute("DELETE FROM search_documents")
    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)
    workspace = SqliteWorkspaceRepository(sqlite_connection).get(workspace_id)
    assert workspace is not None
    backfill_search_index(node_repository, resource_repository, search_index, workspace)

    assert search_service.search(workspace_id, "schemaprobefour") == ()
