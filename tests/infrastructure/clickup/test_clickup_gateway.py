"""Agent-parity test for `pgos_import_clickup_item`: the gateway's thin wrapper routes a
task import through the same `ClickupService` -> `CapturePlanningOrchestrator` pipeline the REST
route uses (EP-2026-012 ST-10), so an MCP caller gets identical replay/conflict semantics."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.clickup_adapters import ClickUpTask
from personal_graph_os.application.clickup_service import ClickupService
from personal_graph_os.application.context_pack_service import ContextPackService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.document_service import DocumentService
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    EdgeService,
    NodeService,
    ResourceService,
    new_workspace,
)
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.application.workflow_chain import WorkflowChainService
from personal_graph_os.infrastructure.clickup.fake_client import FakeClickUpClient
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.mcp.gateway import AgentGatewayService
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteAttachmentRepository,
    SqliteBm25SearchEngine,
    SqliteCollectionRepository,
    SqliteContextPackRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteTagRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork
from personal_graph_os.infrastructure.work_planning.fake_provider import FakeWorkPlanningProvider


def _task() -> ClickUpTask:
    return ClickUpTask(
        id="task-1",
        name="Implement signup",
        description="Add OAuth login",
        url="https://app.clickup.com/t/task-1",
        date_updated=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )


def _build_gateway_with_clickup(
    sqlite_connection: sqlite3.Connection,
    tmp_path,
    *,
    work_planning_service: WorkPlanningService | None = None,
):
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    node_repository = SqliteNodeRepository(sqlite_connection)
    edge_repository = SqliteEdgeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index_repository = SqliteSearchIndexRepository(sqlite_connection)
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731

    node_service = NodeService(
        workspace_repository,
        node_repository,
        unit_of_work_factory,
        search_index=search_index_repository,
    )
    edge_service = EdgeService(
        workspace_repository, node_repository, edge_repository, unit_of_work_factory
    )
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        unit_of_work_factory,
        search_index=search_index_repository,
    )
    search_service = SearchService(
        node_repository,
        resource_repository,
        SqliteDocumentRepository(sqlite_connection),
        SqliteBm25SearchEngine(sqlite_connection),
    )
    file_service = FileService(
        node_repository,
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        LocalManagedFileStore(tmp_path / "managed-root"),
        SqlitePendingFileOperationRepository(sqlite_connection),
        unit_of_work_factory,
        current_machine_name=lambda: "laptop",
    )
    workflow_chain_service = WorkflowChainService(
        workspace_repository, node_repository, unit_of_work_factory
    )
    discovery_service = DiscoveryService(
        workspace_repository, resource_repository, resource_service, unit_of_work_factory
    )
    context_pack_service = ContextPackService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        file_service,
        SqliteContextPackRepository(sqlite_connection),
    )
    activity_service = ActivityService(SqliteActivityEventRepository(sqlite_connection))
    work_item_service = WorkItemService(
        workspace_repository,
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        unit_of_work_factory,
    )
    document_service = DocumentService(
        workspace_repository,
        SqliteDocumentRepository(sqlite_connection),
        SqliteDocumentVersionRepository(sqlite_connection),
        SqliteDocumentLinkRepository(sqlite_connection),
        SqliteCollectionRepository(sqlite_connection),
        SqliteTagRepository(sqlite_connection),
        node_repository,
        unit_of_work_factory,
    )
    capture_service = CaptureService(
        workspace_repository,
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteIdempotencyReceiptRepository(sqlite_connection),
        resource_service,
        unit_of_work_factory,
    )
    orchestrator = CapturePlanningOrchestrator(
        capture_service, work_planning_service, unit_of_work_factory
    )
    clickup_service = ClickupService(
        FakeClickUpClient({_task().id: _task()}),
        orchestrator,
        workspace_repository,
        unit_of_work_factory,
    )
    gateway = AgentGatewayService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        search_service,
        file_service,
        node_service=node_service,
        edge_service=edge_service,
        resource_service=resource_service,
        workflow_chain_service=workflow_chain_service,
        discovery_service=discovery_service,
        context_pack_service=context_pack_service,
        activity_service=activity_service,
        document_service=document_service,
        work_item_service=work_item_service,
        enrichment_service=None,
        extraction_service=None,
        capture_planning_orchestrator=orchestrator,
        clickup_service=clickup_service,
        unit_of_work_factory=unit_of_work_factory,
    )
    return gateway, workspace_repository


def test_import_clickup_item_routes_through_the_shared_capture_pipeline(
    sqlite_connection: sqlite3.Connection,
    tmp_path,
) -> None:
    gateway, workspace_repository = _build_gateway_with_clickup(sqlite_connection, tmp_path)
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository.save(workspace)

    result = gateway.import_clickup_item(
        workspace.id,
        task_id="task-1",
        intent="save_raw",
        actor_name="agent:test",
        repository_node_id=None,
        reason="import selected ClickUp task",
    )

    assert result["document_id"] is not None
    assert result["was_replayed"] is False
    assert result["needs_clarification"] is False
    assert result["pending_operations"] == []
    assert "plan" not in result


def test_import_clickup_item_reports_pending_plan_and_the_plan_reference(
    sqlite_connection: sqlite3.Connection,
    tmp_path,
) -> None:
    """Regression for review finding S10-F04: a planned import must surface `pending_operations`
    and the plan's deep-linkable ids to the MCP agent, not silently drop them."""
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    work_item_service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        unit_of_work_factory,
    )
    work_planning_service = WorkPlanningService(
        work_item_service, FakeWorkPlanningProvider(), unit_of_work_factory
    )
    gateway, workspace_repository = _build_gateway_with_clickup(
        sqlite_connection, tmp_path, work_planning_service=work_planning_service
    )
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository.save(workspace)

    result = gateway.import_clickup_item(
        workspace.id,
        task_id="task-1",
        intent="plan_work",
        actor_name="agent:test",
        repository_node_id=None,
        reason="import selected ClickUp task",
    )

    assert result["pending_operations"] == ["plan"]
    plan = result["plan"]
    assert plan["epic_id"] is not None
    assert plan["plan_document_id"] is not None
    assert isinstance(plan["story_ids"], list)
    assert isinstance(plan["task_ids"], list)


def test_import_clickup_item_replays_by_task_id_without_duplicating(
    sqlite_connection: sqlite3.Connection,
    tmp_path,
) -> None:
    gateway, workspace_repository = _build_gateway_with_clickup(sqlite_connection, tmp_path)
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository.save(workspace)

    first = gateway.import_clickup_item(
        workspace.id,
        task_id="task-1",
        intent="save_raw",
        actor_name="agent:test",
        repository_node_id=None,
        reason="import selected ClickUp task",
    )
    second = gateway.import_clickup_item(
        workspace.id,
        task_id="task-1",
        intent="save_raw",
        actor_name="agent:test",
        repository_node_id=None,
        reason="import selected ClickUp task",
    )

    assert first["document_id"] == second["document_id"]
    assert second["was_replayed"] is True


def test_import_clickup_item_rejects_an_unknown_intent(
    sqlite_connection: sqlite3.Connection,
    tmp_path,
) -> None:
    gateway, workspace_repository = _build_gateway_with_clickup(sqlite_connection, tmp_path)
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository.save(workspace)

    with pytest.raises(ValueError):
        gateway.import_clickup_item(
            workspace.id,
            task_id="task-1",
            intent="not-an-intent",
            actor_name="agent:test",
            repository_node_id=None,
            reason="import selected ClickUp task",
        )
