"""ST-06.4: Context Pack creation, listing, deletion, and materialization against live state."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.context_pack_service import (
    ContextPackNotFoundError,
    ContextPackSelectionError,
    ContextPackService,
    ContextPackTokenBudgetError,
)
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import EdgeService, NodeService, new_workspace
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.files import Attachment
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
    file_service = FileService(
        node_repository,
        attachment_repository,
        file_reference_repository,
        LocalManagedFileStore(tmp_path / "managed-root"),
        pending_file_operation_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
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
        "attachment_repository": attachment_repository,
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


def test_create_within_rejects_a_node_that_does_not_exist(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F02: a selection is resolved and required to exist before persistence, never
    silently accepted and reported "missing" only at materialize time."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    missing_node_id = NodeId("does-not-exist")

    with pytest.raises(ContextPackSelectionError):
        with ctx["unit_of_work_factory"]() as unit_of_work:
            service.create_within(
                unit_of_work,
                ctx["workspace_id"],
                "pack",
                node_ids=(missing_node_id,),
                inclusion_reasons={missing_node_id: "referenced but does not exist"},
            )


def test_create_within_rejects_a_node_owned_by_a_different_workspace(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F02: a cross-workspace Node/Edge selection is rejected, never accepted and
    materialized under a different workspace's Context Pack."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    other_task_type = NodeType(name="Task")
    other_workspace = ensure_semantic_schema(
        new_workspace("Other").model_copy(update={"node_types": (other_task_type,)})
    )
    workspace_repository.save(other_workspace)
    foreign_node = node_service.capture(other_workspace.id, other_task_type.id, "Foreign")

    with pytest.raises(ContextPackSelectionError):
        with ctx["unit_of_work_factory"]() as unit_of_work:
            service.create_within(
                unit_of_work,
                ctx["workspace_id"],
                "pack",
                node_ids=(foreign_node.id,),
                inclusion_reasons={foreign_node.id: "cross-workspace"},
            )


def test_materialize_reports_missing_and_archived_nodes(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    node_a_id = ctx["node_a"].id
    node_b_id = ctx["node_b"].id
    edge_id = ctx["edge"].id
    node_c = node_service.capture(ctx["workspace_id"], ctx["node_a"].node_type_id, "Third node")

    node_service.archive(node_b_id)

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_b_id, node_c.id),
            edge_ids=(edge_id,),
            inclusion_reasons={
                node_a_id: "primary source",
                node_b_id: "related finding",
                node_c.id: "referenced, disappears after pack creation",
                edge_id: "connects the two",
            },
        )

    # Simulate the selected Node disappearing entirely after a valid pack already selected it
    # -- the only way `missing` can legitimately occur once creation requires existence (F02).
    sqlite_connection.execute("DELETE FROM nodes WHERE id = ?", (node_c.id,))
    sqlite_connection.commit()

    materialization = service.materialize(pack.id)

    assert {node.id for node in materialization.nodes} == {node_a_id, node_b_id}
    assert materialization.archived_node_ids == (node_b_id,)
    assert materialization.missing_node_ids == (node_c.id,)
    assert {edge.id for edge in materialization.edges} == {edge_id}


def test_materialize_resolves_and_reports_unresolved_evidence_pointers(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    attachment_repository = ctx["attachment_repository"]
    attachment = Attachment(
        node_id=ctx["node_a"].id,
        file_name="notes.txt",
        mime_type="text/plain",
        size_bytes=4,
        checksum_sha256="0" * 64,
        storage_relative_path="a/notes.txt",
    )
    attachment_repository.save(attachment)
    pointer = f"attachment:{attachment.id}"

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            evidence_pointers=(pointer,),
            inclusion_reasons={pointer: "would-be evidence"},
        )

    # Simulate the attachment disappearing after a valid pack already resolved it (F02).
    attachment_repository.delete(attachment.id)

    materialization = service.materialize(pack.id)

    assert materialization.evidence == ()
    assert materialization.unresolved_evidence_pointers == (pointer,)


def test_materialize_omits_members_deterministically_once_token_limit_is_exceeded(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    node_a_id = ctx["node_a"].id
    node_b_id = ctx["node_b"].id
    node_service.update(node_b_id, title="Second node", body="x" * 4_000)

    # Learn the token cost of a manifest-plus-node_a-alone pack, then budget the two-node pack
    # just enough (plus a small margin for the extra manifest entry) to fit node_a but never
    # node_b's much larger body (ST06-F03: the estimate covers the full returned wire shape).
    with ctx["unit_of_work_factory"]() as unit_of_work:
        baseline_pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "baseline",
            node_ids=(node_a_id,),
            inclusion_reasons={node_a_id: "primary source"},
        )
    token_limit = service.materialize(baseline_pack.id).estimated_tokens + 50

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_b_id),
            inclusion_reasons={node_a_id: "primary source", node_b_id: "related finding"},
            token_limit=token_limit,
        )

    materialization = service.materialize(pack.id)

    assert {node.id for node in materialization.nodes} == {node_a_id}
    assert materialization.omitted_for_token_budget == (node_b_id,)


def test_materialize_never_exceeds_token_limit_with_many_large_omissions(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F04 round-2 re-review: with 120 large selected Nodes and a tight `token_limit`,
    the previous implementation decided omissions before charging the eventual
    `omitted_for_token_budget` list and final wrapper fields, so the *reported* estimate
    (3,888 tokens) could exceed the configured limit (2,651) even though every individual
    decision looked correct in isolation. The exact inequality -- not merely a deterministic
    omission order -- must hold no matter how many members end up omitted."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    workspace_id = ctx["workspace_id"]
    node_type_id = ctx["node_a"].node_type_id

    node_ids = []
    for i in range(120):
        node = node_service.capture(workspace_id, node_type_id, f"Large node {i}")
        node_service.update(node.id, body="x" * 2_500)
        node_ids.append(node.id)
    inclusion_reasons = {node_id: "bulk probe" for node_id in node_ids}

    # Discover this selection's true non-omittable floor (manifest plus a full omission
    # report of every selected node) directly from the same rejection this fix now performs,
    # rather than hard-coding a token count that would silently drift with the estimator.
    with pytest.raises(
        ContextPackTokenBudgetError, match=r"floor \((\d+) estimated tokens\)"
    ) as excinfo:
        with ctx["unit_of_work_factory"]() as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace_id,
                "probe-floor",
                node_ids=tuple(node_ids),
                inclusion_reasons=inclusion_reasons,
                token_limit=1,
            )
    floor_match = re.search(r"floor \((\d+) estimated tokens\)", str(excinfo.value))
    assert floor_match is not None
    floor_tokens = int(floor_match.group(1))

    # A limit just above the floor still forces near-total omission (each node's body is far
    # larger than its own share of the margin), directly exercising the many-large-omission
    # path while remaining a token_limit this selection can legally satisfy.
    token_limit = floor_tokens + 50
    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            workspace_id,
            "many-large-nodes",
            node_ids=tuple(node_ids),
            inclusion_reasons=inclusion_reasons,
            token_limit=token_limit,
        )

    materialization = service.materialize(pack.id)

    assert materialization.estimated_tokens <= token_limit
    assert len(materialization.omitted_for_token_budget) > 0


def test_create_within_rejects_a_token_limit_smaller_than_the_manifest(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F03 re-review: the immutable manifest (name/selection/`inclusion_reasons`) can
    never be omitted, so a `token_limit` too small for it alone must reject creation instead
    of persisting a pack whose materialization can never obey its own configured budget."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_a_id = ctx["node_a"].id

    with pytest.raises(ContextPackTokenBudgetError):
        with ctx["unit_of_work_factory"]() as unit_of_work:
            service.create_within(
                unit_of_work,
                ctx["workspace_id"],
                "pack",
                node_ids=(node_a_id,),
                inclusion_reasons={node_a_id: "x" * 1_000},
                token_limit=1,
            )

    assert ctx["context_pack_service"].list_by_workspace(ctx["workspace_id"]) == ()


def test_materialize_estimated_tokens_covers_wrapper_report_fields(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F03 re-review: the reported estimate must include the always-returned
    archived/missing/unresolved-report fields, not just manifest + included members -- two
    packs with identical included content but a different number of `missing_node_ids` must
    report different totals."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_service: NodeService = ctx["node_service"]
    node_a_id = ctx["node_a"].id
    extra_missing = [
        node_service.capture(ctx["workspace_id"], ctx["node_a"].node_type_id, f"Doomed {i}").id
        for i in range(5)
    ]

    with ctx["unit_of_work_factory"]() as unit_of_work:
        small_pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "small",
            node_ids=(node_a_id,),
            inclusion_reasons={node_a_id: "primary source"},
        )
    with ctx["unit_of_work_factory"]() as unit_of_work:
        large_pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "large",
            node_ids=(node_a_id, *extra_missing),
            inclusion_reasons={
                node_a_id: "primary source",
                **{node_id: "will be deleted" for node_id in extra_missing},
            },
        )

    for node_id in extra_missing:
        sqlite_connection.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
    sqlite_connection.commit()

    small_materialization = service.materialize(small_pack.id)
    large_materialization = service.materialize(large_pack.id)

    assert small_materialization.missing_node_ids == ()
    assert large_materialization.missing_node_ids == tuple(extra_missing)
    assert {node.id for node in large_materialization.nodes} == {node_a_id}
    assert large_materialization.estimated_tokens > small_materialization.estimated_tokens


def test_materialize_estimated_tokens_is_deterministic_across_a_fresh_service_instance(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """ST06-F03 re-review: the estimate is computed fresh from current DB state every call, so
    a brand new `ContextPackService` bound to the same connection (simulating a process
    restart) must report an identical total and identical omissions."""
    ctx = _fixture(sqlite_connection, tmp_path)
    service: ContextPackService = ctx["context_pack_service"]
    node_a_id = ctx["node_a"].id
    node_b_id = ctx["node_b"].id

    with ctx["unit_of_work_factory"]() as unit_of_work:
        pack = service.create_within(
            unit_of_work,
            ctx["workspace_id"],
            "pack",
            node_ids=(node_a_id, node_b_id),
            inclusion_reasons={node_a_id: "primary source", node_b_id: "related finding"},
        )

    first = service.materialize(pack.id)

    restarted_service = ContextPackService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteNodeRepository(sqlite_connection),
        SqliteEdgeRepository(sqlite_connection),
        SqliteResourceRepository(sqlite_connection),
        FileService(
            SqliteNodeRepository(sqlite_connection),
            SqliteAttachmentRepository(sqlite_connection),
            SqliteFileReferenceRepository(sqlite_connection),
            LocalManagedFileStore(tmp_path / "managed-root"),
            SqlitePendingFileOperationRepository(sqlite_connection),
            lambda: SqliteResearchUnitOfWork(sqlite_connection),
            current_machine_name=lambda: "laptop",
        ),
        SqliteContextPackRepository(sqlite_connection),
    )
    second = restarted_service.materialize(pack.id)

    assert second.estimated_tokens == first.estimated_tokens
    assert second.omitted_for_token_budget == first.omitted_for_token_budget
    assert {node.id for node in second.nodes} == {node.id for node in first.nodes}
