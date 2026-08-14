from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from personal_graph_os.application.research_dashboard import ResearchDashboardService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.workflow_chain import WorkflowChainService, WorkflowChainStep
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteResearchSettingsRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    edge_repository = SqliteEdgeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    settings_repository = SqliteResearchSettingsRepository(sqlite_connection)

    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    workflow_chain_service = WorkflowChainService(
        workspace_repository, node_repository, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )
    dashboard = ResearchDashboardService(
        workspace_repository, resource_repository, edge_repository, settings_repository
    )
    return dashboard, resource_service, workflow_chain_service, settings_repository, workspace.id


def _capture_resource(resource_service: ResourceService, workspace_id, suffix: str):
    resource, _ = resource_service.create_or_reuse(
        workspace_id,
        f"Resource {suffix}",
        f"https://arxiv.org/abs/2401.0000{suffix}",
        kind=ResourceKind.PAPER,
    )
    return resource


def test_inbox_returns_only_inbox_resources(sqlite_connection: sqlite3.Connection) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    inbox_resource = _capture_resource(resource_service, workspace_id, "1")
    reading_resource = _capture_resource(resource_service, workspace_id, "2")
    resource_service.update(reading_resource.id, lifecycle_status=ResourceLifecycleStatus.READING)

    results = dashboard.inbox(workspace_id)

    assert [r.id for r in results] == [inbox_resource.id]


def test_continue_reading_includes_reading_and_paused(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    reading = _capture_resource(resource_service, workspace_id, "1")
    resource_service.update(reading.id, lifecycle_status=ResourceLifecycleStatus.READING)
    paused = _capture_resource(resource_service, workspace_id, "2")
    resource_service.update(
        paused.id, lifecycle_status=ResourceLifecycleStatus.PAUSED, next_action_dismissed=True
    )
    inbox = _capture_resource(resource_service, workspace_id, "3")

    results = {r.id for r in dashboard.continue_reading(workspace_id)}

    assert results == {reading.id, paused.id}
    assert inbox.id not in results


def test_stale_uses_injected_as_of_and_workspace_stale_after_days(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, _wf, settings_repository, workspace_id = _fixture(
        sqlite_connection
    )
    settings_repository.save(
        WorkspaceResearchSettings(workspace_id=workspace_id, stale_after_days=7)
    )
    resource = _capture_resource(resource_service, workspace_id, "1")
    resource_service.update(resource.id, lifecycle_status=ResourceLifecycleStatus.READING)

    just_under_boundary = resource.last_activity_at + timedelta(days=7) - timedelta(seconds=1)
    just_over_boundary = resource.last_activity_at + timedelta(days=7) + timedelta(seconds=1)

    assert dashboard.stale(workspace_id, as_of=just_under_boundary) == ()
    assert [r.id for r in dashboard.stale(workspace_id, as_of=just_over_boundary)] == [resource.id]


def test_stale_excludes_applied_and_archived_even_when_inactive(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    applied = _capture_resource(resource_service, workspace_id, "1")
    resource_service.update(applied.id, lifecycle_status=ResourceLifecycleStatus.APPLIED)
    archived = _capture_resource(resource_service, workspace_id, "2")
    resource_service.update(archived.id, lifecycle_status=ResourceLifecycleStatus.ARCHIVED)

    far_future = datetime.now(UTC) + timedelta(days=3650)

    assert dashboard.stale(workspace_id, as_of=far_future) == ()


def test_needs_takeaway_excludes_resources_that_already_have_one(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    without_takeaway = _capture_resource(resource_service, workspace_id, "1")
    with_takeaway = _capture_resource(resource_service, workspace_id, "2")
    resource_service.update(with_takeaway.id, takeaways=("An insight",))

    results = {r.id for r in dashboard.needs_takeaway(workspace_id)}

    assert results == {without_takeaway.id}


def test_unlinked_excludes_a_resource_once_the_workflow_chain_connects_a_takeaway(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, workflow_chain_service, _settings, workspace_id = _fixture(
        sqlite_connection
    )
    resource = _capture_resource(resource_service, workspace_id, "1")

    assert [r.id for r in dashboard.unlinked(workspace_id)] == [resource.id]

    workflow_chain_service.advance(
        workspace_id, resource.node_id, WorkflowChainStep.RESOURCE_TO_TAKEAWAY, title="Insight"
    )

    assert dashboard.unlinked(workspace_id) == ()


def test_applied_returns_only_applied_resources(sqlite_connection: sqlite3.Connection) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    applied = _capture_resource(resource_service, workspace_id, "1")
    resource_service.update(applied.id, lifecycle_status=ResourceLifecycleStatus.APPLIED)
    inbox = _capture_resource(resource_service, workspace_id, "2")

    results = {r.id for r in dashboard.applied(workspace_id)}

    assert results == {applied.id}
    assert inbox.id not in results


def test_archived_resources_disappear_from_every_bucket(
    sqlite_connection: sqlite3.Connection,
) -> None:
    dashboard, resource_service, _wf, _settings, workspace_id = _fixture(sqlite_connection)
    archived = _capture_resource(resource_service, workspace_id, "1")
    resource_service.archive(archived.id)
    live = _capture_resource(resource_service, workspace_id, "2")

    assert archived.id not in {r.id for r in dashboard.inbox(workspace_id)}
    assert archived.id not in {r.id for r in dashboard.continue_reading(workspace_id)}
    assert archived.id not in {r.id for r in dashboard.stale(workspace_id, as_of=datetime.now(UTC))}
    assert archived.id not in {r.id for r in dashboard.needs_takeaway(workspace_id)}
    assert archived.id not in {r.id for r in dashboard.unlinked(workspace_id)}
    assert archived.id not in {r.id for r in dashboard.applied(workspace_id)}
    assert live.id in {r.id for r in dashboard.inbox(workspace_id)}
