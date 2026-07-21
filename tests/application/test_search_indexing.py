"""NodeService/ResourceService keep `search_documents` current as the sole mutation paths;
`SearchService` resolves hits back to live Node/Resource state (archived exclusion, in
particular, is always evaluated live rather than baked into the index)."""

from __future__ import annotations

import sqlite3

from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.services import NodeService, ResourceService, new_workspace
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.schema import NodeType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    task_type = NodeType(name="Task")
    resource_type = NodeType(name="Resource", system_key="resource")
    workspace = new_workspace("Personal").model_copy(
        update={"node_types": (task_type, resource_type)}
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)

    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)

    node_service = NodeService(workspace_repository, node_repository, search_index=search_index)
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


def test_search_is_workspace_scoped(sqlite_connection: sqlite3.Connection) -> None:
    node_service, _resource_service, search_service, workspace_id, task_type = _fixture(
        sqlite_connection
    )
    node_service.capture(workspace_id, task_type.id, "Shared keyword")

    other_workspace = new_workspace("Other")
    SqliteWorkspaceRepository(sqlite_connection).save(other_workspace)

    assert search_service.search(other_workspace.id, "shared") == ()
    assert len(search_service.search(workspace_id, "shared")) == 1
