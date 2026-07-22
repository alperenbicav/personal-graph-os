from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.services import (
    ResearchSettingsService,
    WorkspaceNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.identifiers import WorkspaceId, new_id
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResearchSettingsRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _service(sqlite_connection: sqlite3.Connection) -> tuple[ResearchSettingsService, WorkspaceId]:
    workspace = new_workspace("Personal")
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    service = ResearchSettingsService(
        workspace_repository,
        SqliteResearchSettingsRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return service, workspace.id


def test_get_or_default_returns_the_product_default_when_unset(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _service(sqlite_connection)

    settings = service.get_or_default(workspace_id)

    assert settings.stale_after_days == 14


def test_update_persists_and_get_or_default_returns_it(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _service(sqlite_connection)

    service.update(workspace_id, stale_after_days=30)

    assert service.get_or_default(workspace_id).stale_after_days == 30


def test_get_or_default_rejects_an_unknown_workspace(sqlite_connection: sqlite3.Connection) -> None:
    service, _workspace_id = _service(sqlite_connection)

    with pytest.raises(WorkspaceNotFoundError):
        service.get_or_default(WorkspaceId(new_id()))
