"""ST-06.4: Context Pack creation, listing, deletion, and materialization against live state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.context_pack_service import (
    ContextPackNotFoundError,
    ContextPackService,
)
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import EdgeService, NodeService, new_workspace
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
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
    edge_type = EdgeType(name="relates_to")
    workspace = ensure_semantic_schema(
        new_workspace("Personal").model_copy(
            update={"node_types": (task_type,), "edge_types": (edge_type,)}
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
    context_pack_repository = SqliteContextPackRepository(sqlite_connection)

    node_service = NodeService(
        workspace_repository, node_repository, search_index=search_index_repository
    )
    edge_service = EdgeService(workspace_repository, node_repository, edge_repository)
    file_service = FileService(
        node_repository,
        attachment_repository,
        file_reference_repository,
        LocalManagedFileStore(tmp_path / "managed-root"),
        pending_file_operation_repository,
        current_machine_name=lambda: "laptop",
    )

    context_pack_service = ContextPackService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        file_service,
        context_pack_repository,
    )

    node_a = node_service.capture(workspace.id, task_type.id, "First node")
    node_b = node_service.capture(workspace.id, task_type.id, "Second node")
    edge = edge_service.connect(workspace.id, edge_type.id, node_a.id, node_b.id)

    return {
        "context_pack_service": context_pack_service,
        "workspace_id": workspace.id,
        "node_a": node_a,
        "node_b": node_b,
        "edge": edge,
        "node_service": node_service,
        "unit_of_work_factory": lambda: SqliteResearchUnitOfWork(sqlite_connection),
    }


def test_create_within_dedupes_ids_in_caller_order(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_a_id = ctx["node_a"].id

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_a_id),
            inclusion_reasons={node_a_id: "primary source"},
        )

    assert pack.node_ids == (node_a_id,)


def test_create_within_missing_inclusion_reason_is_rejected(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]

    with pytest.raises(InvariantViolationError):
        with ctx["unit_of_work_factory"]() as unit_of_work:
            service.create_within(
                unit_of_work,
                ctx["workspace_id"],
                "pack",
                node_ids=(ctx["node_a"].id,),
                inclusion_reasons={},
            )


def test_get_list_and_delete_within_round_trip(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_a_id = ctx["node_a"].id

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id,),
            inclusion_reasons={node_a_id: "primary source"},
        )

    assert service.get(pack.id) == pack
    assert pack in service.list_by_workspace(ctx["workspace_id"])

    with ctx["unit_of_work_factory"]() as unit_of_work:
        deleted = service.delete_within(unit_of_work, pack.id)

    assert deleted.id == pack.id
    with pytest.raises(ContextPackNotFoundError):
        service.get(pack.id)


def test_materialize_reports_missing_and_archived_nodes(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    node_a_id = ctx["node_a"].id
    node_b_id = ctx["node_b"].id
    edge_id = ctx["edge"].id
    missing_node_id = NodeId("does-not-exist")

    node_service.archive(node_b_id)

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_b_id, missing_node_id),
            edge_ids=(edge_id,),
            inclusion_reasons={
                node_a_id: "primary source",
                node_b_id: "related finding",
                missing_node_id: "referenced but later deleted",
                edge_id: "connects the two",
            },
        )

    materialization = service.materialize(pack.id)

    assert {node.id for node in materialization.nodes} == {node_a_id, node_b_id}
    assert materialization.archived_node_ids == (node_b_id,)
    assert materialization.missing_node_ids == (missing_node_id,)
    assert {edge.id for edge in materialization.edges} == {edge_id}


def test_materialize_resolves_and_reports_unresolved_evidence_pointers(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    unresolved_pointer = "attachment:does-not-exist"

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            evidence_pointers=(unresolved_pointer,),
            inclusion_reasons={unresolved_pointer: "would-be evidence"},
        )

    materialization = service.materialize(pack.id)

    assert materialization.evidence == ()
    assert materialization.unresolved_evidence_pointers == (unresolved_pointer,)


def test_materialize_omits_members_deterministically_once_token_limit_is_exceeded(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    node_a_id = ctx["node_a"].id
    node_b_id = ctx["node_b"].id
    node_service.update(node_b_id, title="Second node", body="x" * 400)

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_b_id),
            inclusion_reasons={node_a_id: "primary source", node_b_id: "related finding"},
            token_limit=5,
        )

    materialization = service.materialize(pack.id)

    assert {node.id for node in materialization.nodes} == {node_a_id}
    assert materialization.omitted_for_token_budget == (node_b_id,)
