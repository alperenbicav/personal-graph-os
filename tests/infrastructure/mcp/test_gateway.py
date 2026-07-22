from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.context_pack_service import ContextPackService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    EdgeService,
    NodeService,
    ResourceService,
    new_workspace,
)
from personal_graph_os.application.workflow_chain import WorkflowChainService
from personal_graph_os.domain.identifiers import NodeId, ResourceId, WorkspaceId
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.mcp.gateway import (
    AgentGatewayService,
    GatewayNotFoundError,
    GatewayValidationError,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteAttachmentRepository,
    SqliteContextPackRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
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
        workspace_repository, node_repository, search_index=search_index_repository
    )
    edge_service = EdgeService(workspace_repository, node_repository, edge_repository)
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index_repository,
    )
    search_service = SearchService(node_repository, resource_repository, search_index_repository)
    file_service = FileService(
        node_repository,
        attachment_repository,
        file_reference_repository,
        LocalManagedFileStore(tmp_path / "managed-root"),
        pending_file_operation_repository,
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
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return {
        "gateway": gateway,
        "node_service": node_service,
        "edge_service": edge_service,
        "resource_service": resource_service,
        "file_service": file_service,
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
    assert [node.title for node in unlimited] == ["Alpha", "Beta", "Gamma"]


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

    hits = ctx["gateway"].search(ctx["workspace_id"], "attention")

    assert [hit.node.title for hit in hits] == ["Attention Is All You Need"]


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
