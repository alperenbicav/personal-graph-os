from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.services import (
    SavedViewNotFoundError,
    SavedViewService,
    new_workspace,
)
from personal_graph_os.domain.identifiers import SavedViewId, WorkspaceId, new_id
from personal_graph_os.domain.views import (
    FilterClause,
    FilterField,
    FilterOperator,
    ProjectionQuery,
    SortClause,
    SortDirection,
    ViewKind,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteSavedViewRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _saved_view_service(
    sqlite_connection: sqlite3.Connection,
) -> tuple[SavedViewService, WorkspaceId]:
    workspace = new_workspace("Personal")
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    saved_view_service = SavedViewService(
        workspace_repository,
        SqliteSavedViewRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return saved_view_service, workspace.id


def test_create_persists_a_typed_query_and_reloads_it_unchanged(
    sqlite_connection: sqlite3.Connection,
) -> None:
    saved_view_service, workspace_id = _saved_view_service(sqlite_connection)
    query = ProjectionQuery(
        filters=(
            FilterClause(
                field=FilterField.IS_ARCHIVED, operator=FilterOperator.EQUALS, value=False
            ),
        ),
        sort=(SortClause(field=FilterField.CREATED_AT, direction=SortDirection.DESC),),
    )

    created = saved_view_service.create(workspace_id, "Inbox", ViewKind.TABLE, query)
    reloaded = saved_view_service.get(created.id)

    assert reloaded.to_projection_query() == query
    assert reloaded.name == "Inbox"
    assert reloaded.view_kind == ViewKind.TABLE


def test_update_replaces_query_and_preserves_identity(
    sqlite_connection: sqlite3.Connection,
) -> None:
    saved_view_service, workspace_id = _saved_view_service(sqlite_connection)
    created = saved_view_service.create(workspace_id, "Inbox", ViewKind.TABLE, ProjectionQuery())

    new_query = ProjectionQuery(
        sort=(SortClause(field=FilterField.UPDATED_AT, direction=SortDirection.ASC),)
    )
    updated = saved_view_service.update(created.id, name="Renamed", query=new_query)

    assert updated.id == created.id
    assert updated.name == "Renamed"
    assert updated.to_projection_query() == new_query


def test_update_without_a_query_keeps_the_existing_one(
    sqlite_connection: sqlite3.Connection,
) -> None:
    saved_view_service, workspace_id = _saved_view_service(sqlite_connection)
    query = ProjectionQuery(
        sort=(SortClause(field=FilterField.CREATED_AT, direction=SortDirection.DESC),)
    )
    created = saved_view_service.create(workspace_id, "Inbox", ViewKind.TABLE, query)

    updated = saved_view_service.update(created.id, name="Renamed")

    assert updated.to_projection_query() == query


def test_list_by_workspace_returns_only_that_workspaces_views(
    sqlite_connection: sqlite3.Connection,
) -> None:
    saved_view_service, workspace_id = _saved_view_service(sqlite_connection)
    saved_view_service.create(workspace_id, "Inbox", ViewKind.TABLE, ProjectionQuery())
    saved_view_service.create(workspace_id, "Board", ViewKind.KANBAN, ProjectionQuery())

    views = saved_view_service.list_by_workspace(workspace_id)

    assert {view.name for view in views} == {"Inbox", "Board"}


def test_delete_removes_the_saved_view(sqlite_connection: sqlite3.Connection) -> None:
    saved_view_service, workspace_id = _saved_view_service(sqlite_connection)
    created = saved_view_service.create(workspace_id, "Inbox", ViewKind.TABLE, ProjectionQuery())

    saved_view_service.delete(created.id)

    with pytest.raises(SavedViewNotFoundError):
        saved_view_service.get(created.id)


def test_get_raises_for_an_unknown_saved_view(sqlite_connection: sqlite3.Connection) -> None:
    saved_view_service, _workspace_id = _saved_view_service(sqlite_connection)

    with pytest.raises(SavedViewNotFoundError):
        saved_view_service.get(SavedViewId(new_id()))
