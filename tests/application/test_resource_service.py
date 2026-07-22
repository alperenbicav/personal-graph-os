from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    ResourceNodeTypeMissingError,
    ResourceNotFoundError,
    ResourceService,
    WorkspaceNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId, new_id
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _resource_service(
    sqlite_connection: sqlite3.Connection,
) -> tuple[ResourceService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return service, workspace.id


def test_create_or_reuse_creates_a_resource_and_its_backing_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)

    resource, was_created = service.create_or_reuse(
        workspace_id, "A great paper", "https://arxiv.org/abs/2401.00001"
    )

    assert was_created is True
    assert resource.kind is ResourceKind.PAPER
    assert resource.canonical_identifier == "arxiv:2401.00001"
    assert resource.lifecycle_status is ResourceLifecycleStatus.INBOX


def test_create_or_reuse_deduplicates_by_canonical_identity_without_a_second_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)

    first, first_created = service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )
    second, second_created = service.create_or_reuse(
        workspace_id, "Same paper, different title", "https://arxiv.org/abs/2401.00001v2"
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.node_id == first.node_id


def test_create_or_reuse_deduplicates_doi_tracking_variants(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)

    first, first_created = service.create_or_reuse(
        workspace_id, "A paper", "https://doi.org/10.1000/xyz123?utm_source=one"
    )
    second, second_created = service.create_or_reuse(
        workspace_id, "Same paper", "https://doi.org/10.1000/xyz123?utm_source=two"
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id


def test_create_or_reuse_enriches_a_missing_source_url_on_reuse(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A candidate imported once via a bare identifier (no URL) can later be enriched with a
    real URL on a duplicate import, without creating a second resource."""
    service, workspace_id = _resource_service(sqlite_connection)
    created, _ = service.create_or_reuse(workspace_id, "A repo", "github:octocat/hello-world")
    assert created.source_url == "https://github.com/octocat/hello-world"

    reused, was_created = service.create_or_reuse(
        workspace_id, "A repo", "https://github.com/octocat/Hello-World"
    )
    assert was_created is False
    assert reused.source_url == "https://github.com/octocat/hello-world"


def test_create_or_reuse_rejects_unknown_workspace(sqlite_connection: sqlite3.Connection) -> None:
    service, _workspace_id = _resource_service(sqlite_connection)
    with pytest.raises(WorkspaceNotFoundError):
        service.create_or_reuse(WorkspaceId(new_id()), "x", "https://example.com/a")


def test_create_or_reuse_rejects_a_workspace_missing_the_resource_role(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace = new_workspace("Bare")  # no semantic roles ensured
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    service = ResourceService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteResourceRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    with pytest.raises(ResourceNodeTypeMissingError):
        service.create_or_reuse(workspace.id, "x", "https://example.com/a")


def test_update_rejects_unknown_resource(sqlite_connection: sqlite3.Connection) -> None:
    service, _workspace_id = _resource_service(sqlite_connection)
    with pytest.raises(ResourceNotFoundError):
        service.update(ResourceId(new_id()), lifecycle_status=ResourceLifecycleStatus.REVIEWED)


def test_update_advances_last_activity_only_on_a_meaningful_change(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    resource, _ = service.create_or_reuse(workspace_id, "A paper", "https://example.com/paper")

    no_op = service.update(resource.id)
    assert no_op.last_activity_at == resource.last_activity_at

    changed = service.update(resource.id, lifecycle_status=ResourceLifecycleStatus.READING)
    assert changed.last_activity_at >= resource.last_activity_at
    assert changed.lifecycle_status is ResourceLifecycleStatus.READING


def test_update_sets_and_clears_progress_percent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    resource, _ = service.create_or_reuse(workspace_id, "A paper", "https://example.com/paper")
    assert resource.progress_percent is None

    updated = service.update(resource.id, progress_percent=42)
    assert updated.progress_percent == 42

    cleared = service.update(updated.id, clear_progress_percent=True)
    assert cleared.progress_percent is None


def test_update_rejects_a_progress_percent_out_of_bounds(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    resource, _ = service.create_or_reuse(workspace_id, "A paper", "https://example.com/paper")

    with pytest.raises(InvariantViolationError):
        service.update(resource.id, progress_percent=101)


def test_update_pausing_requires_next_action_or_explicit_dismissal(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    resource, _ = service.create_or_reuse(workspace_id, "A paper", "https://example.com/paper")

    with pytest.raises(InvariantViolationError):
        service.update(resource.id, lifecycle_status=ResourceLifecycleStatus.PAUSED)

    paused = service.update(
        resource.id, lifecycle_status=ResourceLifecycleStatus.PAUSED, next_action_dismissed=True
    )
    assert paused.lifecycle_status is ResourceLifecycleStatus.PAUSED
    assert paused.next_action_dismissed is True


def test_archive_sets_lifecycle_status_to_archived(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    resource, _ = service.create_or_reuse(workspace_id, "A paper", "https://example.com/paper")

    archived = service.archive(resource.id)

    assert archived.lifecycle_status is ResourceLifecycleStatus.ARCHIVED


def test_list_by_workspace_returns_every_resource(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    service.create_or_reuse(workspace_id, "A", "https://example.com/a")
    service.create_or_reuse(workspace_id, "B", "https://example.com/b")

    resources = service.list_by_workspace(workspace_id)

    assert len(resources) == 2
