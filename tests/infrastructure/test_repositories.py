from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.domain.activity import DiscoveredCandidate, DiscoveryOutcome, DiscoveryRun
from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    AttachmentId,
    NodeId,
    ResourceId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import Resource, ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)
from personal_graph_os.domain.views import SavedView, ViewKind
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteAttachmentRepository,
    SqliteCanvasPlacementRepository,
    SqliteCanvasRepository,
    SqliteDiscoveryRunRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteResearchSettingsRepository,
    SqliteResourceRepository,
    SqliteSavedViewRepository,
    SqliteWorkspaceRepository,
)


def test_workspace_round_trips_nested_schema(sqlite_connection: sqlite3.Connection) -> None:
    task_type = NodeType(
        name="Task",
        field_definitions=(FieldDefinition(name="priority", field_type=FieldType.TEXT),),
        status_definitions=(),
    )
    workspace = Workspace(
        name="Personal",
        node_types=(task_type,),
        edge_types=(EdgeType(name="relates_to"),),
    )

    repository = SqliteWorkspaceRepository(sqlite_connection)
    repository.save(workspace)
    reloaded = repository.get(workspace.id)

    assert reloaded is not None
    assert reloaded.name == "Personal"
    assert len(reloaded.node_types) == 1
    assert reloaded.node_types[0].name == "Task"
    assert reloaded.node_types[0].field_definitions[0].name == "priority"
    assert reloaded.edge_types[0].name == "relates_to"


def test_node_round_trips_field_values(sqlite_connection: sqlite3.Connection) -> None:
    task_type = NodeType(
        name="Task",
        field_definitions=(FieldDefinition(name="priority", field_type=FieldType.TEXT),),
    )
    workspace = Workspace(name="Personal", node_types=(task_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)

    priority_field = task_type.field_definitions[0]
    node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="Write report",
        field_values={priority_field.id: "high"},
    )
    node_repository = SqliteNodeRepository(sqlite_connection)
    node_repository.save(node)
    reloaded = node_repository.get(node.id)

    assert reloaded is not None
    assert reloaded.title == "Write report"
    assert reloaded.field_values[priority_field.id] == "high"


def test_node_round_trips_a_date_field_value(sqlite_connection: sqlite3.Connection) -> None:
    """Regression for ST-01 review finding: a DATE field value must be JSON-serializable
    (an ISO-8601 string), so `SqliteNodeRepository.save()` must not raise and the value
    must round-trip unchanged."""
    task_type = NodeType(
        name="Task",
        field_definitions=(FieldDefinition(name="due_date", field_type=FieldType.DATE),),
    )
    workspace = Workspace(name="Personal", node_types=(task_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)

    due_date_field = task_type.field_definitions[0]
    node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="File taxes",
        field_values={due_date_field.id: "2026-08-01"},
    )
    node.validate_against(task_type)

    node_repository = SqliteNodeRepository(sqlite_connection)
    node_repository.save(node)
    reloaded = node_repository.get(node.id)

    assert reloaded is not None
    assert reloaded.field_values[due_date_field.id] == "2026-08-01"


def test_node_repository_excludes_archived_by_default(
    sqlite_connection: sqlite3.Connection,
) -> None:
    task_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(task_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)

    node_repository = SqliteNodeRepository(sqlite_connection)
    active = Node(workspace_id=workspace.id, node_type_id=task_type.id, title="Active")
    archived = Node(
        workspace_id=workspace.id, node_type_id=task_type.id, title="Archived", is_archived=True
    )
    node_repository.save(active)
    node_repository.save(archived)

    visible = node_repository.list_by_workspace(workspace.id)
    everything = node_repository.list_by_workspace(workspace.id, include_archived=True)

    assert {node.id for node in visible} == {active.id}
    assert {node.id for node in everything} == {active.id, archived.id}


def test_edge_round_trips_and_lists_incident_edges(sqlite_connection: sqlite3.Connection) -> None:
    node_type = NodeType(name="Task")
    edge_type = EdgeType(name="relates_to")
    workspace = Workspace(name="Personal", node_types=(node_type,), edge_types=(edge_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)

    node_repository = SqliteNodeRepository(sqlite_connection)
    source = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Source")
    target = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Target")
    node_repository.save(source)
    node_repository.save(target)

    edge_repository = SqliteEdgeRepository(sqlite_connection)
    edge = Edge(
        workspace_id=workspace.id,
        edge_type_id=edge_type.id,
        source_node_id=source.id,
        target_node_id=target.id,
    )
    edge_repository.save(edge)

    assert edge_repository.get(edge.id) is not None
    assert {e.id for e in edge_repository.list_incident_to_node(source.id)} == {edge.id}
    assert {e.id for e in edge_repository.list_incident_to_node(target.id)} == {edge.id}


def test_save_deletes_field_and_status_definitions_removed_from_the_node_type(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression: `save()` is a full-replace upsert. A field/status definition dropped
    from the in-memory `NodeType` must not resurrect on the next read (it previously did,
    since `_save_node_types()` only ever inserted/updated rows and never deleted any)."""
    kept_field = FieldDefinition(name="title_field", field_type=FieldType.TEXT)
    removed_field = FieldDefinition(name="scratch_field", field_type=FieldType.TEXT)
    kept_status = StatusDefinition(name="Open")
    removed_status = StatusDefinition(name="Scratch")
    task_type = NodeType(
        name="Task",
        field_definitions=(kept_field, removed_field),
        status_definitions=(kept_status, removed_status),
    )
    workspace = Workspace(name="Personal", node_types=(task_type,))
    repository = SqliteWorkspaceRepository(sqlite_connection)
    repository.save(workspace)

    narrowed_task_type = NodeType(
        id=task_type.id,
        name=task_type.name,
        icon=task_type.icon,
        color_hex=task_type.color_hex,
        field_definitions=(kept_field,),
        status_definitions=(kept_status,),
    )
    narrowed_workspace = Workspace(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        node_types=(narrowed_task_type,),
        edge_types=workspace.edge_types,
    )
    repository.save(narrowed_workspace)

    reloaded = repository.get(workspace.id)
    assert reloaded is not None
    reloaded_task_type = reloaded.node_types[0]
    assert {f.id for f in reloaded_task_type.field_definitions} == {kept_field.id}
    assert {s.id for s in reloaded_task_type.status_definitions} == {kept_status.id}


def test_save_deletes_edge_types_removed_from_the_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    kept_edge_type = EdgeType(name="relates_to")
    removed_edge_type = EdgeType(name="scratch_relation")
    workspace = Workspace(name="Personal", edge_types=(kept_edge_type, removed_edge_type))
    repository = SqliteWorkspaceRepository(sqlite_connection)
    repository.save(workspace)

    narrowed_workspace = Workspace(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        node_types=workspace.node_types,
        edge_types=(kept_edge_type,),
    )
    repository.save(narrowed_workspace)

    reloaded = repository.get(workspace.id)
    assert reloaded is not None
    assert {et.id for et in reloaded.edge_types} == {kept_edge_type.id}


def test_canvas_placement_round_trips(sqlite_connection: sqlite3.Connection) -> None:
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)

    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)

    canvas = Canvas(workspace_id=workspace.id, name="Main")
    SqliteCanvasRepository(sqlite_connection).save(canvas)

    placement = CanvasPlacement(
        canvas_id=canvas.id, node_id=node.id, position_x=10.0, position_y=20.0
    )
    placement_repository = SqliteCanvasPlacementRepository(sqlite_connection)
    placement_repository.save(placement)

    reloaded = placement_repository.get(placement.id)
    assert reloaded is not None
    assert reloaded.position_x == 10.0
    assert reloaded.position_y == 20.0
    assert [p.id for p in placement_repository.list_by_canvas(canvas.id)] == [placement.id]


def _workspace_with_resource_node_type(
    sqlite_connection: sqlite3.Connection,
) -> tuple[Workspace, NodeType]:
    resource_type = NodeType(name="Resource")
    workspace = Workspace(name="Personal", node_types=(resource_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    return workspace, resource_type


def test_resource_round_trips_lifecycle_and_dismissal(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace, resource_type = _workspace_with_resource_node_type(sqlite_connection)
    node = Node(workspace_id=workspace.id, node_type_id=resource_type.id, title="A paper")
    SqliteNodeRepository(sqlite_connection).save(node)
    resource = Resource(
        workspace_id=workspace.id,
        node_id=node.id,
        kind=ResourceKind.PAPER,
        canonical_identifier="arxiv:2401.00001",
        lifecycle_status=ResourceLifecycleStatus.PAUSED,
        next_action_dismissed=True,
        open_questions=("why?",),
        takeaways=("interesting",),
    )
    repository = SqliteResourceRepository(sqlite_connection)

    repository.save(resource)
    reloaded = repository.get(resource.id)

    assert reloaded is not None
    assert reloaded.lifecycle_status is ResourceLifecycleStatus.PAUSED
    assert reloaded.next_action_dismissed is True
    assert reloaded.open_questions == ("why?",)
    assert reloaded.takeaways == ("interesting",)
    assert repository.get_by_node(node.id) is not None
    assert repository.get_by_canonical_identifier(workspace.id, "arxiv:2401.00001") is not None
    assert repository.list_by_workspace(workspace.id) == (reloaded,)


def test_resource_repository_returns_none_for_unknown_lookups(
    sqlite_connection: sqlite3.Connection,
) -> None:
    repository = SqliteResourceRepository(sqlite_connection)
    assert repository.get(ResourceId("missing")) is None
    assert repository.get_by_node(NodeId("missing")) is None
    assert (
        repository.get_by_canonical_identifier(WorkspaceId("missing-workspace"), "missing-id")
        is None
    )


def test_saved_view_round_trips_and_deletes(sqlite_connection: sqlite3.Connection) -> None:
    workspace = Workspace(name="Personal")
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    saved_view = SavedView(
        workspace_id=workspace.id,
        name="Research Inbox",
        view_kind=ViewKind.TABLE,
        filter_definition={"lifecycle_status": "inbox"},
        sort_definition={"field": "last_activity_at", "direction": "desc"},
    )
    repository = SqliteSavedViewRepository(sqlite_connection)

    repository.save(saved_view)
    reloaded = repository.get(saved_view.id)
    assert reloaded is not None
    assert reloaded.filter_definition == {"lifecycle_status": "inbox"}
    assert repository.list_by_workspace(workspace.id) == (reloaded,)

    repository.delete(saved_view.id)
    assert repository.get(saved_view.id) is None


def test_discovery_run_round_trips_candidates(sqlite_connection: sqlite3.Connection) -> None:
    workspace = Workspace(name="Personal")
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    discovery_run = DiscoveryRun(
        workspace_id=workspace.id,
        agent_identity="claude",
        instruction="find papers about graph databases",
        sources_searched=("arxiv",),
        filters_interpreted={"kind": "paper"},
        candidates=(
            DiscoveredCandidate(
                raw_identifier="arxiv:1",
                canonical_identifier="arxiv:1",
                title="A",
                outcome=DiscoveryOutcome.IMPORTED,
            ),
            DiscoveredCandidate(
                raw_identifier="arxiv:2",
                canonical_identifier="arxiv:2",
                title="B",
                outcome=DiscoveryOutcome.REUSED,
                reason="duplicate",
            ),
        ),
    )
    repository = SqliteDiscoveryRunRepository(sqlite_connection)

    repository.save(discovery_run)
    reloaded = repository.get(discovery_run.id)

    assert reloaded is not None
    assert reloaded.imported_count == 1
    assert reloaded.skipped_count == 1
    assert reloaded.candidates[0].was_imported is True
    assert reloaded.candidates[1].was_imported is False
    assert reloaded.candidates[1].reason == "duplicate"
    assert repository.list_by_workspace(workspace.id) == (reloaded,)


def test_research_settings_defaults_and_round_trips(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace = Workspace(name="Personal")
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    repository = SqliteResearchSettingsRepository(sqlite_connection)
    assert repository.get(workspace.id) is None

    repository.save(WorkspaceResearchSettings(workspace_id=workspace.id, stale_after_days=30))
    reloaded = repository.get(workspace.id)

    assert reloaded is not None
    assert reloaded.stale_after_days == 30

    repository.save(WorkspaceResearchSettings(workspace_id=workspace.id, stale_after_days=7))
    final = repository.get(workspace.id)
    assert final is not None
    assert final.stale_after_days == 7


def _node_in_a_fresh_workspace(sqlite_connection: sqlite3.Connection) -> Node:
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)
    return node


def test_attachment_round_trips_and_deletes(sqlite_connection: sqlite3.Connection) -> None:
    node = _node_in_a_fresh_workspace(sqlite_connection)
    attachment = Attachment(
        node_id=node.id,
        file_name="notes.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        checksum_sha256="a" * 64,
        storage_relative_path="attachment-1",
    )
    repository = SqliteAttachmentRepository(sqlite_connection)

    repository.save(attachment)
    reloaded = repository.get(attachment.id)

    assert reloaded == attachment
    assert repository.list_by_node(node.id) == (attachment,)

    repository.delete(attachment.id)
    assert repository.get(attachment.id) is None
    assert repository.list_by_node(node.id) == ()


def test_attachment_save_rejects_an_unknown_node(sqlite_connection: sqlite3.Connection) -> None:
    attachment = Attachment(
        node_id=NodeId(new_id()),
        file_name="notes.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        checksum_sha256="a" * 64,
        storage_relative_path="attachment-1",
    )

    with pytest.raises(sqlite3.IntegrityError):
        SqliteAttachmentRepository(sqlite_connection).save(attachment)


def test_file_reference_round_trips_and_updates_verification_state(
    sqlite_connection: sqlite3.Connection,
) -> None:
    node = _node_in_a_fresh_workspace(sqlite_connection)
    reference = FileReference(
        node_id=node.id,
        machine_name="laptop",
        relative_path="repo/README.md",
        repository_name="apilex-agent",
        absolute_path="/Users/alice/repo/README.md",
    )
    repository = SqliteFileReferenceRepository(sqlite_connection)

    repository.save(reference)
    reloaded = repository.get(reference.id)

    assert reloaded is not None
    assert reloaded.is_missing is False
    assert reloaded.last_verified_at is None
    assert repository.list_by_node(node.id) == (reloaded,)

    verified = reference.model_copy(
        update={"is_missing": True, "last_verified_at": datetime.now(UTC)}
    )
    repository.save(verified)
    reverified = repository.get(reference.id)

    assert reverified is not None
    assert reverified.is_missing is True
    assert reverified.last_verified_at is not None

    repository.delete(reference.id)
    assert repository.get(reference.id) is None


def test_file_reference_save_rejects_an_unknown_node(sqlite_connection: sqlite3.Connection) -> None:
    reference = FileReference(
        node_id=NodeId(new_id()), machine_name="laptop", relative_path="repo/README.md"
    )

    with pytest.raises(sqlite3.IntegrityError):
        SqliteFileReferenceRepository(sqlite_connection).save(reference)


def test_pending_file_operation_records_and_removes(sqlite_connection: sqlite3.Connection) -> None:
    repository = SqlitePendingFileOperationRepository(sqlite_connection)

    entry = repository.record(
        attachment_id=AttachmentId(new_id()),
        storage_relative_path="attachment-1",
        quarantine_token=None,
    )

    assert repository.list_all() == (entry,)

    repository.remove(entry.id)
    assert repository.list_all() == ()


def test_pending_file_operation_records_a_quarantine_token(
    sqlite_connection: sqlite3.Connection,
) -> None:
    repository = SqlitePendingFileOperationRepository(sqlite_connection)

    entry = repository.record(
        attachment_id=AttachmentId(new_id()),
        storage_relative_path="attachment-1",
        quarantine_token="trash-token-1",
    )

    (reloaded,) = repository.list_all()
    assert reloaded.quarantine_token == "trash-token-1"
    assert reloaded.id == entry.id
