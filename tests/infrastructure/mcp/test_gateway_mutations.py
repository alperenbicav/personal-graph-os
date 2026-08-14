"""Attributed, idempotent mutation tests (ST-06.2, decisions #13/#14 in `WORK.md`): a real
change and its `ActivityEvent` commit together, a failure leaves neither, an exact replay by
request_id never re-executes, and a no-op/reuse result never invents a mutation event."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.context_pack_service import ContextPackService
from personal_graph_os.application.discovery import DiscoveryCandidateInput, DiscoveryService
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
from personal_graph_os.application.undo_service import UndoConflictError, UndoService
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.workflow_chain import WorkflowChainService, WorkflowChainStep
from personal_graph_os.domain.activity import IdempotencyReceipt
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.identifiers import ContextPackId, NodeId, NodeTypeId
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.mcp.gateway import (
    AgentGatewayService,
    GatewayConflictError,
    GatewayNotFoundError,
    GatewayValidationError,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteAttachmentRepository,
    SqliteBm25SearchEngine,
    SqliteCanvasPlacementRepository,
    SqliteCollectionRepository,
    SqliteContextPackRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteResearchSettingsRepository,
    SqliteResourceRepository,
    SqliteSavedViewRepository,
    SqliteSearchIndexRepository,
    SqliteTagRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _undo_service(sqlite_connection: sqlite3.Connection) -> UndoService:
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index_repository = SqliteSearchIndexRepository(sqlite_connection)
    node_service = NodeService(
        workspace_repository,
        node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index_repository,
    )
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index_repository,
    )
    return UndoService(
        SqliteActivityEventRepository(sqlite_connection),
        node_repository,
        resource_repository,
        SqliteEdgeRepository(sqlite_connection),
        SqliteCanvasPlacementRepository(sqlite_connection),
        SqliteSavedViewRepository(sqlite_connection),
        SqliteResearchSettingsRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        node_service=node_service,
        resource_service=resource_service,
    )


def _build_gateway(sqlite_connection: sqlite3.Connection, tmp_path: Path):
    """Construct a fresh `AgentGatewayService` and its repositories bound to
    `sqlite_connection`, with no in-memory state carried over from any prior instance --
    equivalent to a process restart against the same database (ST06-F01 restart regression)."""
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    node_repository = SqliteNodeRepository(sqlite_connection)
    edge_repository = SqliteEdgeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    search_index_repository = SqliteSearchIndexRepository(sqlite_connection)
    attachment_repository = SqliteAttachmentRepository(sqlite_connection)
    file_reference_repository = SqliteFileReferenceRepository(sqlite_connection)
    pending_file_operation_repository = SqlitePendingFileOperationRepository(sqlite_connection)

    node_service = NodeService(
        workspace_repository,
        node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index_repository,
    )
    edge_service = EdgeService(
        workspace_repository,
        node_repository,
        edge_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
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
        attachment_repository,
        file_reference_repository,
        LocalManagedFileStore(tmp_path / "managed-root"),
        pending_file_operation_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    workflow_chain_service = WorkflowChainService(
        workspace_repository, node_repository, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )
    discovery_service = DiscoveryService(
        workspace_repository,
        resource_repository,
        resource_service,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    context_pack_repository = SqliteContextPackRepository(sqlite_connection)
    context_pack_service = ContextPackService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        file_service,
        context_pack_repository,
    )
    activity_service = ActivityService(SqliteActivityEventRepository(sqlite_connection))
    work_item_service = WorkItemService(
        workspace_repository,
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    document_service = DocumentService(
        workspace_repository,
        SqliteDocumentRepository(sqlite_connection),
        SqliteDocumentVersionRepository(sqlite_connection),
        SqliteDocumentLinkRepository(sqlite_connection),
        SqliteCollectionRepository(sqlite_connection),
        SqliteTagRepository(sqlite_connection),
        node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
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
        capture_planning_orchestrator=None,
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return {
        "gateway": gateway,
        "node_repository": node_repository,
        "edge_repository": edge_repository,
        "resource_repository": resource_repository,
        "context_pack_repository": context_pack_repository,
        "unit_of_work_factory": lambda: SqliteResearchUnitOfWork(sqlite_connection),
    }


def _fixture(sqlite_connection: sqlite3.Connection, tmp_path: Path):
    task_type = NodeType(name="Task")
    resource_type = NodeType(name="Resource", system_key="resource")
    edge_type = EdgeType(name="relates_to")
    workspace = ensure_semantic_schema(
        new_workspace("Personal").model_copy(
            update={"node_types": (task_type, resource_type), "edge_types": (edge_type,)}
        )
    )
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)

    built = _build_gateway(sqlite_connection, tmp_path)
    return {
        **built,
        "workspace_id": workspace.id,
        "task_type": task_type,
        "resource_type": resource_type,
        "edge_type": edge_type,
        "workspace": workspace,
    }


def test_create_node_replay_by_request_id_does_not_create_a_second_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    first = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Attention Is All You Need",
        actor_name="agent-1",
        reason="capture a paper",
        request_id="req-1",
    )
    replay = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Attention Is All You Need",
        actor_name="agent-1",
        reason="capture a paper",
        request_id="req-1",
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["node"]["id"] == first["node"]["id"]
    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_nodes) == 1


def test_create_node_with_a_different_request_id_creates_a_second_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "First",
        actor_name="agent-1",
        reason="r",
        request_id="req-1",
    )
    gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Second",
        actor_name="agent-1",
        reason="r",
        request_id="req-2",
    )

    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_nodes) == 2


def test_update_node_replay_returns_the_same_result_without_reapplying(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Original",
        actor_name="agent-1",
        reason="r",
        request_id="req-create",
    )
    node_id = NodeId(created["node"]["id"])

    first = gateway.update_node(
        node_id,
        title="Updated",
        body=None,
        status_id=None,
        field_values=None,
        actor_name="agent-1",
        reason="fix title",
        request_id="req-update",
    )
    replay = gateway.update_node(
        node_id,
        title="Updated",
        body=None,
        status_id=None,
        field_values=None,
        actor_name="agent-1",
        reason="fix title",
        request_id="req-update",
    )

    assert first["node"]["title"] == "Updated"
    assert replay["replayed"] is True
    assert replay["node"]["id"] == first["node"]["id"]
    stored = ctx["node_repository"].get(node_id)
    assert stored is not None
    assert stored.title == "Updated"


def test_update_node_reused_request_id_with_a_different_payload_is_a_conflict(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F01: a reused `request_id` with a changed payload must never silently replay the
    original result -- it is a bounded conflict, and the second (different) title never
    applies."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Original",
        actor_name="agent-1",
        reason="r",
        request_id="req-create",
    )
    node_id = NodeId(created["node"]["id"])

    gateway.update_node(
        node_id,
        title="Updated",
        body=None,
        status_id=None,
        field_values=None,
        actor_name="agent-1",
        reason="fix title",
        request_id="req-update",
    )

    with pytest.raises(GatewayConflictError):
        gateway.update_node(
            node_id,
            title="A different title that should never apply",
            body=None,
            status_id=None,
            field_values=None,
            actor_name="agent-1",
            reason="fix title",
            request_id="req-update",
        )

    stored = ctx["node_repository"].get(node_id)
    assert stored is not None
    assert stored.title == "Updated"


def test_reused_request_id_across_different_tools_is_a_conflict(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F01: the same `request_id`/actor claimed by one tool must never replay as a
    different tool's result (the original bug mislabeled a Node id as a Context Pack id)."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "First",
        actor_name="agent-1",
        reason="r",
        request_id="shared-key",
    )

    with pytest.raises(GatewayConflictError):
        gateway.create_context_pack(
            ctx["workspace_id"],
            "pack",
            node_ids=(),
            inclusion_reasons={},
            actor_name="agent-1",
            reason="r",
            request_id="shared-key",
        )


def test_create_node_reused_request_id_with_only_a_different_reason_is_a_conflict(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F01 re-review: `reason` determines the persisted `ActivityEvent.reason`, so a
    reused key with every other argument identical but a changed `reason` must never silently
    replay -- it is a bounded conflict, same as a changed business payload."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Attention Is All You Need",
        actor_name="agent-1",
        reason="capture a paper",
        request_id="req-1",
    )

    with pytest.raises(GatewayConflictError):
        gateway.create_node(
            ctx["workspace_id"],
            ctx["task_type"].id,
            "Attention Is All You Need",
            actor_name="agent-1",
            reason="a different reason that should never silently replay",
            request_id="req-1",
        )

    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_nodes) == 1


def test_receipt_survives_a_fresh_gateway_instance_against_the_same_connection(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F01 re-review: a receipt is durable state, not an in-memory cache -- a brand new
    `AgentGatewayService` built against the same connection (simulating a process restart)
    must still replay an exact match and still reject a changed payload."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    first = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Persisted across restart",
        actor_name="agent-1",
        reason="r",
        request_id="req-restart",
    )

    restarted = _build_gateway(sqlite_connection, tmp_path)
    restarted_gateway: AgentGatewayService = restarted["gateway"]

    replay = restarted_gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Persisted across restart",
        actor_name="agent-1",
        reason="r",
        request_id="req-restart",
    )
    assert replay["replayed"] is True
    assert replay["node"]["id"] == first["node"]["id"]

    with pytest.raises(GatewayConflictError):
        restarted_gateway.create_node(
            ctx["workspace_id"],
            ctx["task_type"].id,
            "A different title",
            actor_name="agent-1",
            reason="r",
            request_id="req-restart",
        )

    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_nodes) == 1


def test_create_node_loses_a_concurrent_claim_on_the_same_key_without_double_executing(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F01 re-review: two concurrent callers can both pass the "no receipt yet" lookup
    before either persists one. Simulate the loser of that race by pre-claiming the key with a
    receipt for a different fingerprint (as the winner's completed call would have produced);
    the loser must get a bounded conflict and never execute its own mutation."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    with ctx["unit_of_work_factory"]() as unit_of_work:
        unit_of_work.idempotency_receipts.save_without_commit(
            IdempotencyReceipt(
                workspace_id=ctx["workspace_id"],
                source="mcp",
                actor_name="agent-1",
                request_id="req-race",
                operation="create_node",
                payload_fingerprint="winner-claimed-this-fingerprint",
                result_payload={"node": {"id": "winner-node-id"}},
            )
        )

    with pytest.raises(GatewayConflictError):
        gateway.create_node(
            ctx["workspace_id"],
            ctx["task_type"].id,
            "Loses the race",
            actor_name="agent-1",
            reason="r",
            request_id="req-race",
        )

    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert all_nodes == ()


def test_archive_node_then_get_raises_not_found_after_deletion_is_not_required(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "To archive",
        actor_name="agent-1",
        reason="r",
        request_id="req-create",
    )
    node_id = NodeId(created["node"]["id"])

    result = gateway.archive_node(
        node_id, actor_name="agent-1", reason="done", request_id="req-archive"
    )

    assert result["node"]["is_archived"] is True


def test_archive_node_raises_not_found_for_unknown_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    with pytest.raises(GatewayNotFoundError):
        gateway.archive_node(
            NodeId("does-not-exist"), actor_name="agent-1", reason="r", request_id="req-1"
        )


def test_connect_nodes_replay_does_not_create_a_second_edge(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    a = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "A",
        actor_name="agent-1",
        reason="r",
        request_id="req-a",
    )
    b = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "B",
        actor_name="agent-1",
        reason="r",
        request_id="req-b",
    )

    gateway.connect_nodes(
        ctx["workspace_id"],
        ctx["edge_type"].id,
        NodeId(a["node"]["id"]),
        NodeId(b["node"]["id"]),
        actor_name="agent-1",
        reason="link",
        request_id="req-connect",
    )
    replay = gateway.connect_nodes(
        ctx["workspace_id"],
        ctx["edge_type"].id,
        NodeId(a["node"]["id"]),
        NodeId(b["node"]["id"]),
        actor_name="agent-1",
        reason="link",
        request_id="req-connect",
    )

    assert replay["replayed"] is True
    edges = ctx["edge_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(edges) == 1


def test_create_or_reuse_resource_reuse_is_not_a_replay_and_never_duplicates(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    first = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper",
        "https://example.com/paper",
        kind=ResourceKind.PAPER,
        body="",
        actor_name="agent-1",
        reason="import",
        request_id="req-1",
    )
    reused = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper (dup)",
        "https://example.com/paper",
        kind=ResourceKind.PAPER,
        body="",
        actor_name="agent-1",
        reason="import again",
        request_id="req-2",
    )

    assert first["was_created"] is True
    assert reused["was_created"] is False
    assert reused["replayed"] is False
    assert reused["resource"]["id"] == first["resource"]["id"]


def test_upsert_document_creates_then_appends_a_version_on_matching_title(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    created = gateway.upsert_document(
        ctx["workspace_id"],
        "Agent note",
        "draft",
        kind=DocumentKind.NOTE,
        document_id=None,
        collection_id=None,
        tag_names=(),
        actor_name="agent-1",
        reason="capture",
        request_id="req-1",
    )
    updated = gateway.upsert_document(
        ctx["workspace_id"],
        "Agent note",
        "final",
        kind=DocumentKind.NOTE,
        document_id=None,
        collection_id=None,
        tag_names=(),
        actor_name="agent-1",
        reason="revise",
        request_id="req-2",
    )

    assert created["was_created"] is True
    assert created["version"]["version_number"] == 1
    assert updated["was_created"] is False
    assert updated["document"]["id"] == created["document"]["id"]
    assert updated["version"]["version_number"] == 2
    assert updated["version"]["body_markdown"] == "final"


def test_upsert_document_replay_by_request_id_does_not_append_twice(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    first = gateway.upsert_document(
        ctx["workspace_id"],
        "Agent note",
        "draft",
        kind=DocumentKind.NOTE,
        document_id=None,
        collection_id=None,
        tag_names=(),
        actor_name="agent-1",
        reason="capture",
        request_id="req-1",
    )
    replayed = gateway.upsert_document(
        ctx["workspace_id"],
        "Agent note",
        "draft",
        kind=DocumentKind.NOTE,
        document_id=None,
        collection_id=None,
        tag_names=(),
        actor_name="agent-1",
        reason="capture",
        request_id="req-1",
    )

    assert first["replayed"] is False
    assert replayed["replayed"] is True
    assert replayed["version"]["version_number"] == 1


def test_update_resource_no_op_is_reported_and_not_replayed(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper",
        "https://example.com/paper",
        kind=ResourceKind.PAPER,
        body="",
        actor_name="agent-1",
        reason="import",
        request_id="req-1",
    )

    result = gateway.update_resource(
        created["resource"]["id"],
        lifecycle_status=None,
        next_action=None,
        clear_next_action=False,
        next_action_dismissed=None,
        open_questions=None,
        takeaways=None,
        progress_percent=None,
        clear_progress_percent=False,
        actor_name="agent-1",
        reason="no-op",
        request_id="req-2",
    )

    assert result["modified"] is False
    assert result["replayed"] is False


def test_update_resource_replay_does_not_reapply(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper",
        "https://example.com/paper",
        kind=ResourceKind.PAPER,
        body="",
        actor_name="agent-1",
        reason="import",
        request_id="req-1",
    )

    gateway.update_resource(
        created["resource"]["id"],
        lifecycle_status=ResourceLifecycleStatus.READING,
        next_action=None,
        clear_next_action=False,
        next_action_dismissed=None,
        open_questions=None,
        takeaways=None,
        progress_percent=None,
        clear_progress_percent=False,
        actor_name="agent-1",
        reason="start reading",
        request_id="req-update",
    )
    replay = gateway.update_resource(
        created["resource"]["id"],
        lifecycle_status=ResourceLifecycleStatus.READING,
        next_action=None,
        clear_next_action=False,
        next_action_dismissed=None,
        open_questions=None,
        takeaways=None,
        progress_percent=None,
        clear_progress_percent=False,
        actor_name="agent-1",
        reason="start reading",
        request_id="req-update",
    )

    assert replay["replayed"] is True
    assert replay["resource"]["id"] == created["resource"]["id"]
    stored = gateway.get_resource(created["resource"]["id"])
    assert stored.lifecycle_status == "reading"


def test_update_resource_reused_request_id_with_a_different_payload_is_a_conflict(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper",
        "https://example.com/paper",
        kind=ResourceKind.PAPER,
        body="",
        actor_name="agent-1",
        reason="import",
        request_id="req-1",
    )

    gateway.update_resource(
        created["resource"]["id"],
        lifecycle_status=ResourceLifecycleStatus.READING,
        next_action=None,
        clear_next_action=False,
        next_action_dismissed=None,
        open_questions=None,
        takeaways=None,
        progress_percent=None,
        clear_progress_percent=False,
        actor_name="agent-1",
        reason="start reading",
        request_id="req-update",
    )

    with pytest.raises(GatewayConflictError):
        gateway.update_resource(
            created["resource"]["id"],
            lifecycle_status=ResourceLifecycleStatus.ARCHIVED,
            next_action=None,
            clear_next_action=False,
            next_action_dismissed=None,
            open_questions=None,
            takeaways=None,
            progress_percent=None,
            clear_progress_percent=False,
            actor_name="agent-1",
            reason="start reading",
            request_id="req-update",
        )

    stored = gateway.get_resource(created["resource"]["id"])
    assert stored.lifecycle_status == "reading"


def test_advance_workflow_creates_a_new_node_and_edge_and_is_idempotent(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    workspace = ctx["workspace"]
    resource_node_type = next(nt for nt in workspace.node_types if nt.system_key == "resource")
    source = gateway.create_node(
        ctx["workspace_id"],
        resource_node_type.id,
        "Source resource",
        actor_name="agent-1",
        reason="r",
        request_id="req-source",
    )

    first = gateway.advance_workflow(
        ctx["workspace_id"],
        NodeId(source["node"]["id"]),
        WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
        title="A takeaway",
        existing_target_node_id=None,
        actor_name="agent-1",
        reason="capture takeaway",
        request_id="req-advance",
    )
    replay = gateway.advance_workflow(
        ctx["workspace_id"],
        NodeId(source["node"]["id"]),
        WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
        title="A takeaway",
        existing_target_node_id=None,
        actor_name="agent-1",
        reason="capture takeaway",
        request_id="req-advance",
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["edge"]["id"] == first["edge"]["id"]
    all_edges = ctx["edge_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_edges) == 1


def test_create_node_over_mcp_records_full_attribution_snapshot_and_is_undoable(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST07-F03: an MCP mutation must carry the same bounded before/after snapshot and
    undoability as REST, not a bare event with null snapshots."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    activity_events = SqliteActivityEventRepository(sqlite_connection)

    result = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Agent-captured task",
        actor_name="agent-1",
        reason="captured via chat",
        request_id="req-create-1",
    )

    event = activity_events.get_by_request(ctx["workspace_id"], "mcp", "agent-1", "req-create-1")
    assert event is not None
    assert event.before_state is None
    assert event.after_state is not None
    assert event.after_state["id"] == result["node"]["id"]
    assert event.after_state["title"] == "Agent-captured task"
    assert event.is_undoable is True

    detail = gateway.get_activity_event(ctx["workspace_id"], event.id)
    assert detail.is_undoable is True
    assert detail.after_state is not None
    assert detail.after_state["title"] == "Agent-captured task"


def test_update_node_over_mcp_records_before_after_and_can_be_undone_by_human_rest(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Original title",
        actor_name="agent-1",
        reason="r",
        request_id="req-create-2",
    )
    node_id = NodeId(created["node"]["id"])

    gateway.update_node(
        node_id,
        title="Agent-updated title",
        body=None,
        status_id=None,
        field_values=None,
        actor_name="agent-1",
        reason="agent fixed the title",
        request_id="req-update-1",
    )

    event = activity_events.get_by_request(ctx["workspace_id"], "mcp", "agent-1", "req-update-1")
    assert event is not None
    assert event.before_state is not None
    assert event.before_state["title"] == "Original title"
    assert event.after_state is not None
    assert event.after_state["title"] == "Agent-updated title"
    assert event.is_undoable is True

    # Human REST undo authority can reverse an allowlisted MCP mutation (ST07-F03).
    undo_service = _undo_service(sqlite_connection)
    compensating = undo_service.undo(ctx["workspace_id"], event.id, reason="revert agent edit")
    assert compensating.reverses_event_id == event.id
    restored = ctx["node_repository"].get(node_id)
    assert restored is not None
    assert restored.title == "Original title"


def test_undo_of_an_mcp_event_rejects_when_state_has_gone_stale(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "First title",
        actor_name="agent-1",
        reason="r",
        request_id="req-create-3",
    )
    node_id = NodeId(created["node"]["id"])
    create_event = activity_events.get_by_request(
        ctx["workspace_id"], "mcp", "agent-1", "req-create-3"
    )
    assert create_event is not None

    gateway.update_node(
        node_id,
        title="Second title",
        body=None,
        status_id=None,
        field_values=None,
        actor_name="agent-1",
        reason="r2",
        request_id="req-update-2",
    )

    undo_service = _undo_service(sqlite_connection)
    with pytest.raises(UndoConflictError, match="stale"):
        undo_service.undo(ctx["workspace_id"], create_event.id, reason="too late")


def test_create_or_reuse_resource_over_mcp_records_a_snapshot_and_is_undoable(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    activity_events = SqliteActivityEventRepository(sqlite_connection)

    result = gateway.create_or_reuse_resource(
        ctx["workspace_id"],
        "A paper",
        "https://arxiv.org/abs/2401.00099",
        kind=None,
        body="",
        actor_name="agent-1",
        reason="discovered via search",
        request_id="req-resource-1",
    )

    event = activity_events.get_by_request(ctx["workspace_id"], "mcp", "agent-1", "req-resource-1")
    assert event is not None
    assert event.after_state is not None
    assert event.after_state["id"] == result["resource"]["id"]
    assert event.is_undoable is True


def test_advance_workflow_over_mcp_is_recorded_as_a_non_undoable_compound_step(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The compound (node + edge) workflow-advance write must never be filed as a plain
    undoable edge create -- undoing only the edge would orphan the freshly created node."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    activity_events = SqliteActivityEventRepository(sqlite_connection)
    workspace = ctx["workspace"]
    resource_node_type = next(nt for nt in workspace.node_types if nt.system_key == "resource")
    source = gateway.create_node(
        ctx["workspace_id"],
        resource_node_type.id,
        "Source resource",
        actor_name="agent-1",
        reason="r",
        request_id="req-source-2",
    )

    gateway.advance_workflow(
        ctx["workspace_id"],
        NodeId(source["node"]["id"]),
        WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
        title="A takeaway",
        existing_target_node_id=None,
        actor_name="agent-1",
        reason="capture takeaway",
        request_id="req-advance-2",
    )

    event = activity_events.get_by_request(ctx["workspace_id"], "mcp", "agent-1", "req-advance-2")
    assert event is not None
    assert event.entity_type == "workflow_chain_step"
    assert event.is_undoable is False
    assert event.after_state is not None
    assert event.after_state["step"] == WorkflowChainStep.RESOURCE_TO_TAKEAWAY.value


def test_mutation_validation_error_rolls_back_without_writing_an_event(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    with pytest.raises(UnknownSchemaReferenceError):
        gateway.create_node(
            ctx["workspace_id"],
            NodeTypeId("does-not-exist-node-type"),
            "Should not persist",
            actor_name="agent-1",
            reason="r",
            request_id="req-fail",
        )

    all_nodes = ctx["node_repository"].list_by_workspace(ctx["workspace_id"])
    assert all_nodes == ()


def _candidate(identifier: str, title: str) -> DiscoveryCandidateInput:
    return DiscoveryCandidateInput(identifier=identifier, title=title)


def test_preview_import_is_side_effect_free(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    preview = gateway.preview_import(
        ctx["workspace_id"],
        "import one paper",
        [_candidate("https://example.com/paper", "A Paper")],
    )

    assert len(preview.candidates) == 1
    assert preview.candidates[0].decision == "create"
    assert ctx["resource_repository"].list_by_workspace(ctx["workspace_id"]) == ()


def test_apply_import_replay_by_request_id_does_not_import_twice(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    candidates = [_candidate("https://example.com/paper", "A Paper")]

    first = gateway.apply_import(
        ctx["workspace_id"],
        "import one paper",
        candidates,
        actor_name="agent-1",
        reason="import a paper",
        request_id="req-import-1",
    )
    replay = gateway.apply_import(
        ctx["workspace_id"],
        "import one paper",
        candidates,
        actor_name="agent-1",
        reason="import a paper",
        request_id="req-import-1",
    )

    assert first["replayed"] is False
    assert first["run"]["agent_identity"] == "agent-1"
    assert first["run"]["imported_count"] == 1
    assert replay["replayed"] is True
    assert replay["run"]["id"] == first["run"]["id"]
    all_resources = ctx["resource_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_resources) == 1


def test_apply_import_reapplying_the_same_candidate_reuses_instead_of_duplicating(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    gateway.apply_import(
        ctx["workspace_id"],
        "import one paper",
        [_candidate("https://example.com/paper", "A Paper")],
        actor_name="agent-1",
        reason="import a paper",
        request_id="req-import-1",
    )
    second = gateway.apply_import(
        ctx["workspace_id"],
        "import the same paper again",
        [_candidate("https://example.com/paper", "A Paper")],
        actor_name="agent-1",
        reason="import a paper",
        request_id="req-import-2",
    )

    assert second["replayed"] is False
    assert second["run"]["imported_count"] == 0
    all_resources = ctx["resource_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_resources) == 1


def test_apply_import_too_many_candidates_is_rejected(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    candidates = [
        _candidate(f"https://example.com/{index}", f"Paper {index}") for index in range(51)
    ]

    with pytest.raises(GatewayValidationError):
        gateway.apply_import(
            ctx["workspace_id"],
            "import too many",
            candidates,
            actor_name="agent-1",
            reason="r",
            request_id="req-too-many",
        )

    assert ctx["resource_repository"].list_by_workspace(ctx["workspace_id"]) == ()


def test_create_context_pack_replay_by_request_id_does_not_create_a_second_pack(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Source node",
        actor_name="agent-1",
        reason="r",
        request_id="req-source",
    )
    node_id = created["node"]["id"]

    first = gateway.create_context_pack(
        ctx["workspace_id"],
        "pack",
        node_ids=(node_id,),
        inclusion_reasons={node_id: "primary source"},
        actor_name="agent-1",
        reason="handoff",
        request_id="req-pack-1",
    )
    replay = gateway.create_context_pack(
        ctx["workspace_id"],
        "pack",
        node_ids=(node_id,),
        inclusion_reasons={node_id: "primary source"},
        actor_name="agent-1",
        reason="handoff",
        request_id="req-pack-1",
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["context_pack"]["id"] == first["context_pack"]["id"]
    all_packs = ctx["context_pack_repository"].list_by_workspace(ctx["workspace_id"])
    assert len(all_packs) == 1


def test_create_context_pack_missing_inclusion_reason_is_rejected(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Source node",
        actor_name="agent-1",
        reason="r",
        request_id="req-source",
    )
    node_id = created["node"]["id"]

    with pytest.raises(GatewayValidationError):
        gateway.create_context_pack(
            ctx["workspace_id"],
            "pack",
            node_ids=(node_id,),
            inclusion_reasons={},
            actor_name="agent-1",
            reason="handoff",
            request_id="req-pack-missing-reason",
        )
    assert ctx["context_pack_repository"].list_by_workspace(ctx["workspace_id"]) == ()


def test_get_and_materialize_and_delete_context_pack(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]
    created = gateway.create_node(
        ctx["workspace_id"],
        ctx["task_type"].id,
        "Source node",
        actor_name="agent-1",
        reason="r",
        request_id="req-source",
    )
    node_id = created["node"]["id"]
    pack_result = gateway.create_context_pack(
        ctx["workspace_id"],
        "pack",
        node_ids=(node_id,),
        inclusion_reasons={node_id: "primary source"},
        actor_name="agent-1",
        reason="handoff",
        request_id="req-pack-1",
    )
    pack_id = pack_result["context_pack"]["id"]

    fetched = gateway.get_context_pack(pack_id)
    assert fetched.id == pack_id

    materialized = gateway.materialize_context_pack(pack_id)
    assert [node.id for node in materialized.nodes] == [node_id]

    first_delete = gateway.delete_context_pack(
        ctx["workspace_id"],
        pack_id,
        actor_name="agent-1",
        reason="cleanup",
        request_id="req-delete-1",
    )
    replay_delete = gateway.delete_context_pack(
        ctx["workspace_id"],
        pack_id,
        actor_name="agent-1",
        reason="cleanup",
        request_id="req-delete-1",
    )

    assert first_delete["replayed"] is False
    assert replay_delete["replayed"] is True
    with pytest.raises(GatewayNotFoundError):
        gateway.get_context_pack(pack_id)


def test_get_context_pack_raises_not_found_for_unknown_id(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway: AgentGatewayService = ctx["gateway"]

    with pytest.raises(GatewayNotFoundError):
        gateway.get_context_pack(ContextPackId("does-not-exist"))
