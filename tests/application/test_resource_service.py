from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application import services as services_module
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
    SqliteActivityEventRepository,
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


def _persist_resource_with_no_source_url(
    sqlite_connection: sqlite3.Connection,
    service: ResourceService,
    workspace_id: WorkspaceId,
    raw_source: str,
) -> ResourceId:
    """Simulate a pre-existing resource whose `source_url` was never recorded (e.g. imported
    before URL tracking existed), independent of whether `create_or_reuse` itself can still
    produce one today."""
    created, _ = service.create_or_reuse(workspace_id, "A paper", raw_source)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    resource_repository.save(created.model_copy(update={"source_url": None}))
    return created.id


def test_create_or_reuse_enrichment_records_exactly_one_atomic_activity_event(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """ST07-F08: enriching a previously empty `source_url` on reuse is a real mutation and
    must be audited atomically, unlike a genuine no-op reuse."""
    service, workspace_id = _resource_service(sqlite_connection)
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    resource_id = _persist_resource_with_no_source_url(
        sqlite_connection, service, workspace_id, "arxiv:2401.00001"
    )
    events_before = activity_events.list_by_workspace(workspace_id, limit=100)

    reused, was_created = service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )

    assert was_created is False
    assert reused.id == resource_id
    assert reused.source_url == "https://arxiv.org/abs/2401.00001"
    events_after = activity_events.list_by_workspace(workspace_id, limit=100)
    new_events = [event for event in events_after if event not in events_before]
    assert len(new_events) == 1
    assert new_events[0].entity_type == "resource"
    assert new_events[0].before_state is not None
    assert new_events[0].before_state["source_url"] is None
    assert new_events[0].after_state is not None
    assert new_events[0].after_state["source_url"] == "https://arxiv.org/abs/2401.00001"


def test_create_or_reuse_genuine_no_op_reuse_never_invents_an_activity_event(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _resource_service(sqlite_connection)
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    created, _ = service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )
    assert created.source_url is not None
    events_before = activity_events.list_by_workspace(workspace_id, limit=100)

    _reused, was_created = service.create_or_reuse(
        workspace_id, "Different title", "https://arxiv.org/abs/2401.00001v2"
    )

    assert was_created is False
    events_after = activity_events.list_by_workspace(workspace_id, limit=100)
    assert events_after == events_before


def test_create_or_reuse_enrichment_rolls_back_atomically_on_event_write_failure(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """An event-write failure during enrichment must leave the resource exactly as it was
    (ST07-F08): no partially-applied `source_url`, no orphaned event."""
    service, workspace_id = _resource_service(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    resource_id = _persist_resource_with_no_source_url(
        sqlite_connection, service, workspace_id, "arxiv:2401.00002"
    )
    events_before = activity_events.list_by_workspace(workspace_id, limit=100)

    def _failing_record_activity_event(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated event-write failure")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            services_module, "record_activity_event", _failing_record_activity_event
        )
        with pytest.raises(RuntimeError, match="simulated event-write failure"):
            service.create_or_reuse(workspace_id, "A paper", "https://arxiv.org/abs/2401.00002")
    finally:
        monkeypatch.undo()

    reloaded = resource_repository.get(resource_id)
    assert reloaded is not None
    assert reloaded.source_url is None
    events_after = activity_events.list_by_workspace(workspace_id, limit=100)
    assert events_after == events_before


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
