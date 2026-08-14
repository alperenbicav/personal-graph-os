from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.context_pack_service import ContextPackService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.document_service import DocumentService
from personal_graph_os.application.enrichment_service import EnrichmentService
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
from personal_graph_os.application.workflow_chain import WorkflowChainService
from personal_graph_os.domain.activity import ActivityEvent, ActorKind, MutationAction
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.extraction import ExtractedContent
from personal_graph_os.domain.identifiers import (
    NodeId,
    RelationProposalId,
    ResourceId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.domain.work_items import (
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)
from personal_graph_os.infrastructure.enrichment.fake_provider import FakeEnrichmentProvider
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
    SqliteCollectionRepository,
    SqliteContextPackRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
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
        "document_service": document_service,
        "node_service": node_service,
        "edge_service": edge_service,
        "resource_service": resource_service,
        "file_service": file_service,
        "activity_event_repository": SqliteActivityEventRepository(sqlite_connection),
        "workspace_id": workspace.id,
        "task_type": task_type,
        "resource_type": resource_type,
        "edge_type": edge_type,
    }


def test_get_workspace_returns_summary(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    workspace = ctx["gateway"].get_workspace(ctx["workspace_id"])

    assert workspace.name == "Personal"
    assert workspace.node_type_count >= 2
    assert workspace.edge_type_count >= 1


def test_get_workspace_raises_not_found_for_unknown_workspace(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    with pytest.raises(GatewayNotFoundError):
        ctx["gateway"].get_workspace(WorkspaceId("does-not-exist"))


def test_list_nodes_is_bounded_and_deterministically_ordered(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    for title in ("Alpha", "Beta", "Gamma"):
        ctx["node_service"].capture(ctx["workspace_id"], ctx["task_type"].id, title)

    limited = ctx["gateway"].list_nodes(ctx["workspace_id"], limit=2)
    unlimited = ctx["gateway"].list_nodes(ctx["workspace_id"], limit=100)

    assert len(limited) == 2
    # Newest first (ST06-F07).
    assert [node.title for node in unlimited] == ["Gamma", "Beta", "Alpha"]
    assert [node.title for node in limited] == ["Gamma", "Beta"]


def test_list_nodes_rejects_limit_above_maximum(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    with pytest.raises(GatewayValidationError):
        ctx["gateway"].list_nodes(ctx["workspace_id"], limit=101)


def test_list_nodes_excludes_archived_by_default(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    node = ctx["node_service"].capture(ctx["workspace_id"], ctx["task_type"].id, "Archived me")
    ctx["node_service"].archive(node.id)

    assert ctx["gateway"].list_nodes(ctx["workspace_id"]) == ()
    assert len(ctx["gateway"].list_nodes(ctx["workspace_id"], include_archived=True)) == 1


def test_get_node_raises_not_found(sqlite_connection: sqlite3.Connection, tmp_path: Path) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    with pytest.raises(GatewayNotFoundError):
        ctx["gateway"].get_node(NodeId("does-not-exist"))


def test_list_edges_is_bounded(sqlite_connection: sqlite3.Connection, tmp_path: Path) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    a = ctx["node_service"].capture(ctx["workspace_id"], ctx["task_type"].id, "A")
    b = ctx["node_service"].capture(ctx["workspace_id"], ctx["task_type"].id, "B")
    ctx["edge_service"].connect(ctx["workspace_id"], ctx["edge_type"].id, a.id, b.id)

    edges = ctx["gateway"].list_edges(ctx["workspace_id"])

    assert len(edges) == 1
    assert edges[0].source_node_id == a.id
    assert edges[0].target_node_id == b.id


def test_search_rejects_limit_above_maximum(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    with pytest.raises(GatewayValidationError):
        ctx["gateway"].search(ctx["workspace_id"], "anything", limit=51)


def test_search_finds_captured_node(sqlite_connection: sqlite3.Connection, tmp_path: Path) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    ctx["node_service"].capture(
        ctx["workspace_id"], ctx["task_type"].id, "Attention Is All You Need"
    )

    result = ctx["gateway"].search(ctx["workspace_id"], "attention")

    assert [hit["node"]["title"] for hit in result["hits"]] == ["Attention Is All You Need"]
    assert result["total"] == 1
    assert result["has_more"] is False


def test_list_resources_and_get_resource(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    resource, _was_created = ctx["resource_service"].create_or_reuse(
        ctx["workspace_id"], "Paper", "https://example.com/paper", kind=ResourceKind.PAPER
    )

    resources = ctx["gateway"].list_resources(ctx["workspace_id"])
    fetched = ctx["gateway"].get_resource(resource.id)

    assert [r.id for r in resources] == [resource.id]
    assert fetched.canonical_identifier == resource.canonical_identifier

    with pytest.raises(GatewayNotFoundError):
        ctx["gateway"].get_resource(ResourceId("does-not-exist"))


def test_list_and_get_activity_events_are_bounded_and_workspace_scoped(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    repository = ctx["activity_event_repository"]
    event = ActivityEvent(
        workspace_id=ctx["workspace_id"],
        actor_kind=ActorKind.HUMAN,
        actor_name="human/local-user/rest",
        source="rest",
        entity_type="node",
        entity_id="node-1",
        action=MutationAction.CREATED,
    )
    repository.save(event)

    page = ctx["gateway"].list_activity_events(ctx["workspace_id"])
    assert [item.id for item in page.events] == [event.id]

    fetched = ctx["gateway"].get_activity_event(ctx["workspace_id"], event.id)
    assert fetched.id == event.id

    with pytest.raises(GatewayNotFoundError):
        ctx["gateway"].get_activity_event(WorkspaceId("other-workspace"), event.id)


def test_list_node_evidence_returns_privacy_safe_pointers(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    node = ctx["node_service"].capture(ctx["workspace_id"], ctx["task_type"].id, "Has evidence")
    ctx["file_service"].upload_attachment(
        node.id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello"]
    )
    ctx["file_service"].create_file_reference(
        node.id, machine_name="laptop", relative_path="repo/file.py", repository_name="repo"
    )

    evidence = ctx["gateway"].list_node_evidence(node.id)

    assert {item.kind for item in evidence} == {"attachment", "file_reference"}
    for item in evidence:
        dumped = item.model_dump()
        assert "path" not in str(dumped).lower() or item.kind == "attachment"
    pointers = {item.pointer for item in evidence}
    assert all(pointer.startswith(("attachment:", "file-reference:")) for pointer in pointers)


def test_list_node_evidence_raises_not_found_for_unknown_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)

    with pytest.raises(GatewayNotFoundError):
        ctx["gateway"].list_node_evidence(NodeId("does-not-exist"))


def test_work_item_lifecycle_via_gateway(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST-09 parity: the work-item surface creates/updates/archives/restores/hard-deletes a
    hierarchy through the same service + receipt/event plumbing every MCP mutation uses."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    epic = gateway.create_work_item(
        workspace_id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="An epic",
        body="Epic body",
        status=WorkItemStatus.BACKLOG,
        parent_id=None,
        repository_node_id=None,
        actor_name="agent:test",
        reason="create",
        request_id="req-1",
    )
    assert epic["replayed"] is False
    epic_id = epic["work_item"]["id"]
    assert epic["work_item"]["title"] == "An epic"

    story = gateway.create_work_item(
        workspace_id,
        kind=WorkItemKind.STORY,
        work_type=WorkItemType.RESEARCH,
        title="A story",
        body="",
        status=WorkItemStatus.PLANNED,
        parent_id=WorkItemId(epic_id),
        repository_node_id=None,
        actor_name="agent:test",
        reason="create",
        request_id="req-2",
    )
    story_id = story["work_item"]["id"]
    assert story["work_item"]["parent_id"] == epic_id

    listed = gateway.list_work_items(workspace_id)
    assert {item.id for item in listed} == {epic_id, story_id}
    assert all(not item.is_archived for item in listed)

    updated = gateway.update_work_item(
        WorkItemId(story_id),
        status=WorkItemStatus.IN_PROGRESS,
        priority=WorkItemPriority.HIGH,
        assignee="Ada",
        due_date=None,
        work_type=None,
        blockers=None,
        progress_percent=40,
        repository_node_id=None,
        clear_priority=False,
        clear_due_date=False,
        clear_assignee=False,
        clear_blockers=False,
        clear_progress_percent=False,
        clear_repository_node_id=False,
        actor_name="agent:test",
        reason="update",
        request_id="req-3",
    )
    assert updated["work_item"]["status"] == "in_progress"
    assert updated["work_item"]["priority"] == "high"
    assert updated["work_item"]["progress_percent"] == 40

    archived = gateway.archive_work_item(
        WorkItemId(epic_id), actor_name="agent:test", reason="archive", request_id="req-4"
    )
    assert archived["recoverable"] is True
    assert archived["work_item"]["is_archived"] is True

    archived_list = gateway.list_work_items(workspace_id)
    assert all(item.id != epic_id for item in archived_list)

    restored = gateway.restore_work_item(
        WorkItemId(epic_id), actor_name="agent:test", reason="restore", request_id="req-5"
    )
    assert restored["work_item"]["is_archived"] is False

    with pytest.raises(GatewayValidationError):
        gateway.delete_work_item(
            WorkItemId(story_id),
            destructive=False,
            confirm_id=story_id,
            actor_name="agent:test",
            reason="delete",
            request_id="req-6",
        )

    deleted = gateway.delete_work_item(
        WorkItemId(story_id),
        destructive=True,
        confirm_id=story_id,
        actor_name="agent:test",
        reason="delete",
        request_id="req-7",
    )
    assert deleted["recoverable"] is False
    assert {item.id for item in gateway.list_work_items(workspace_id)} == {epic_id}


def test_work_item_checklist_and_wiki_links_via_gateway(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    epic = gateway.create_work_item(
        workspace_id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        body="",
        status=WorkItemStatus.BACKLOG,
        parent_id=None,
        repository_node_id=None,
        actor_name="agent:test",
        reason="create",
        request_id="req-1",
    )
    epic_id = WorkItemId(epic["work_item"]["id"])

    item = gateway.add_checklist_item(
        epic_id, label="First step", actor_name="agent:test", reason="checklist", request_id="req-2"
    )
    item_id = item["checklist_item"]["id"]
    assert item["checklist_item"]["position"] == 0

    toggled = gateway.update_checklist_item(
        WorkItemChecklistItemId(item_id),
        label=None,
        is_completed=True,
        actor_name="agent:test",
        reason="checklist",
        request_id="req-3",
    )
    assert toggled["checklist_item"]["is_completed"] is True

    document, _ = ctx["document_service"].create_document(
        workspace_id, title="Plan page", kind=DocumentKind.NOTE, source="manual"
    )
    link = gateway.attach_document(
        epic_id,
        document_id=document.id,
        actor_name="agent:test",
        reason="link",
        request_id="req-4",
    )
    assert link["document_link_id"]

    gateway.detach_document(
        epic_id,
        document_id=document.id,
        actor_name="agent:test",
        reason="unlink",
        request_id="req-5",
    )

    gateway.remove_checklist_item(
        WorkItemChecklistItemId(item_id),
        actor_name="agent:test",
        reason="checklist",
        request_id="req-6",
    )


def test_document_and_resource_lifecycle_via_gateway(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    document, _ = ctx["document_service"].create_document(
        workspace_id, title="A page", kind=DocumentKind.NOTE, source="manual"
    )
    archived = gateway.archive_document(
        document.id, actor_name="agent:test", reason="archive", request_id="req-1"
    )
    assert archived["recoverable"] is True
    assert archived["document"]["is_archived"] is True

    restored = gateway.restore_document(
        document.id, actor_name="agent:test", reason="restore", request_id="req-2"
    )
    assert restored["document"]["is_archived"] is False

    with pytest.raises(GatewayValidationError):
        gateway.delete_document(
            document.id,
            destructive=True,
            confirm_id="wrong",
            actor_name="agent:test",
            reason="delete",
            request_id="req-3",
        )
    deleted = gateway.delete_document(
        document.id,
        destructive=True,
        confirm_id=document.id,
        actor_name="agent:test",
        reason="delete",
        request_id="req-4",
    )
    assert deleted["recoverable"] is False

    resource, _ = ctx["resource_service"].create_or_reuse(
        workspace_id, "A paper", "arxiv:1234.5678", kind=ResourceKind.PAPER
    )
    restored_resource = gateway.restore_resource(
        resource.id, actor_name="agent:test", reason="restore", request_id="req-5"
    )
    assert restored_resource["resource"]["lifecycle_status"] == "inbox"


def test_work_item_hard_delete_is_atomic_and_rolls_back(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """S9-F01: delete + audit + receipt must commit together; a raised exception inside the
    unit of work rolls the hard delete back instead of leaving a committed node gone."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    epic = gateway.create_work_item(
        workspace_id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Atomic epic",
        body="",
        status=WorkItemStatus.BACKLOG,
        parent_id=None,
        repository_node_id=None,
        actor_name="agent:test",
        reason="create",
        request_id="atomic-1",
    )
    work_item_id = WorkItemId(epic["work_item"]["id"])
    node_id = NodeId(epic["work_item"]["node_id"])

    with pytest.raises(RuntimeError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            gateway._work_item_service.delete_within(unit_of_work, workspace_id, work_item_id)
            raise RuntimeError("boom after delete")

    assert gateway.get_work_item(work_item_id) is not None
    assert SqliteNodeRepository(sqlite_connection).get(node_id) is not None


def test_enrich_resource_with_a_configured_provider_persists_and_replays(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """S9-F02: the configured path must not nest a transaction (the enrichment service opens
    its own), must persist a profile result, and must replay idempotently by request_id."""
    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    resource, _ = ctx["resource_service"].create_or_reuse(
        workspace_id, "A paper", "arxiv:1234.5678", kind=ResourceKind.PAPER
    )

    class _StubExtraction:
        def extract(self, **kwargs):
            from personal_graph_os.domain.extraction import EvidenceKind, ExtractionEvidence

            return ExtractedContent(
                resource_kind=ResourceKind.PAPER,
                canonical_identifier="arxiv:1234.5678",
                title="A paper",
                evidence=(
                    ExtractionEvidence(
                        kind=EvidenceKind.METADATA_LOOKUP,
                        adapter_name="stub",
                        source_reference="arxiv:1234.5678",
                        content_hash="a" * 64,
                        byte_length=3,
                    ),
                ),
            )

    gateway._enrichment_service = EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        FakeEnrichmentProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    gateway._extraction_service = _StubExtraction()

    first = gateway.enrich_resource(
        resource.id, actor_name="agent:test", reason="enrich", request_id="enr-1"
    )
    assert first["replayed"] is False
    assert first["profile_id"]

    replay = gateway.enrich_resource(
        resource.id, actor_name="agent:test", reason="enrich", request_id="enr-1"
    )
    assert replay["replayed"] is True
    assert replay["profile_id"] == first["profile_id"]


def test_relation_proposal_re_accept_and_re_reject_are_guarded(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """S9-F04: an already-resolved proposal cannot be re-accepted or (if applied) rejected."""
    from personal_graph_os.domain.enrichment import RelationProposalStatus
    from personal_graph_os.domain.extraction import EvidenceKind, ExtractionEvidence

    ctx = _fixture(sqlite_connection, tmp_path)
    gateway = ctx["gateway"]
    workspace_id = ctx["workspace_id"]

    resource, _ = ctx["resource_service"].create_or_reuse(
        workspace_id, "A paper", "arxiv:1234.5678", kind=ResourceKind.PAPER
    )
    target_node = ctx["node_service"].capture(workspace_id, ctx["task_type"].id, "Target")

    class _StubExtraction:
        def extract(self, **kwargs):
            return ExtractedContent(
                resource_kind=ResourceKind.PAPER,
                canonical_identifier="arxiv:1234.5678",
                title="A paper",
                evidence=(
                    ExtractionEvidence(
                        kind=EvidenceKind.METADATA_LOOKUP,
                        adapter_name="stub",
                        source_reference="arxiv:1234.5678",
                        content_hash="a" * 64,
                        byte_length=3,
                    ),
                ),
            )

    gateway._enrichment_service = EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        FakeEnrichmentProvider(auto_relate_to=(("Target", "relates_to", 0.5),)),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        auto_apply_confidence_threshold=0.8,
    )
    gateway._extraction_service = _StubExtraction()

    gateway.enrich_resource(
        resource.id, actor_name="agent:test", reason="enrich", request_id="prop-0"
    )
    proposals = gateway.list_relation_proposals(
        workspace_id, status=RelationProposalStatus.NEEDS_REVIEW, limit=10
    )
    assert len(proposals) == 1
    proposal_id = RelationProposalId(proposals[0].id)

    accepted = gateway.accept_relation_proposal(
        proposal_id,
        target_node_id=target_node.id,
        actor_name="agent:test",
        reason="accept",
        request_id="prop-1",
    )
    assert accepted["proposal"]["status"] == "auto_applied"

    with pytest.raises(GatewayConflictError):
        gateway.accept_relation_proposal(
            proposal_id,
            target_node_id=target_node.id,
            actor_name="agent:test",
            reason="accept again",
            request_id="prop-2",
        )

    with pytest.raises(GatewayConflictError):
        gateway.reject_relation_proposal(
            proposal_id, actor_name="agent:test", reason="reject", request_id="prop-3"
        )

    assert (
        gateway._edges.get(
            next(
                edge.id
                for edge in gateway._edges.list_by_workspace(workspace_id)
                if edge.source_node_id == resource.node_id and edge.target_node_id == target_node.id
            )
        )
        is not None
    )
