from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import WorkspaceNotFoundError, new_workspace
from personal_graph_os.application.work_item_service import (
    InvalidChecklistOrderError,
    InvalidWorkItemParentError,
    RepositoryNodeNotFoundError,
    WorkItemChecklistItemNotFoundError,
    WorkItemDocumentNotFoundError,
    WorkItemNodeTypeMissingError,
    WorkItemNotFoundError,
    WorkItemService,
    WorkItemUpdatePatch,
)
from personal_graph_os.domain.documents import Document, DocumentKind, DocumentLinkTargetType
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import (
    DocumentId,
    NodeId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.work_items import (
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteBm25SearchEngine,
    SqliteDocumentRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _service(sqlite_connection: sqlite3.Connection) -> tuple[WorkItemService, Workspace]:
    workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Personal")))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return service, workspace


def test_create_within_creates_an_epic_with_no_parent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        epic, node = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="An epic",
            body="Epic description",
            source="agent:test",
        )

    assert epic.kind is WorkItemKind.EPIC
    assert epic.parent_id is None
    assert epic.node_id == node.id
    assert node.title == "An epic"
    assert node.body == "Epic description"


def test_story_accepts_an_epic_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        epic, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="Epic",
            source="agent:test",
        )
        story, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.STORY,
            work_type=WorkItemType.FEATURE,
            title="Story",
            source="agent:test",
            parent_id=epic.id,
        )

    assert story.parent_id == epic.id


def test_story_rejects_a_non_epic_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        story, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.STORY,
            work_type=WorkItemType.FEATURE,
            title="Standalone story",
            source="agent:test",
        )

    with pytest.raises(InvalidWorkItemParentError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=WorkItemKind.STORY,
                work_type=WorkItemType.FEATURE,
                title="Child story",
                source="agent:test",
                parent_id=story.id,
            )


def test_task_rejects_an_epic_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        epic, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="Epic",
            source="agent:test",
        )

    with pytest.raises(InvalidWorkItemParentError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=WorkItemKind.TASK,
                work_type=WorkItemType.FEATURE,
                title="Task under epic",
                source="agent:test",
                parent_id=epic.id,
            )


def test_task_accepts_a_story_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        story, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.STORY,
            work_type=WorkItemType.FEATURE,
            title="Story",
            source="agent:test",
        )
        task, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.TASK,
            work_type=WorkItemType.FEATURE,
            title="Task",
            source="agent:test",
            parent_id=story.id,
        )

    assert task.parent_id == story.id
    assert task.status is WorkItemStatus.BACKLOG


def test_create_within_rejects_a_parent_from_a_different_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_a = _service(sqlite_connection)
    workspace_b = ensure_semantic_schema(seed_default_schema(new_workspace("Other")))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace_b)
    node_type_a = service.require_node_type(workspace_a)
    node_type_b = service.require_node_type(workspace_b)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        epic_b, _ = service.create_within(
            unit_of_work,
            workspace_b,
            node_type_b,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="Epic in B",
            source="agent:test",
        )

    with pytest.raises(InvalidWorkItemParentError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace_a,
                node_type_a,
                kind=WorkItemKind.STORY,
                work_type=WorkItemType.FEATURE,
                title="Story in A",
                source="agent:test",
                parent_id=epic_b.id,
            )


def test_create_within_rejects_a_nonexistent_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with pytest.raises(InvalidWorkItemParentError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=WorkItemKind.STORY,
                work_type=WorkItemType.FEATURE,
                title="Orphan story",
                source="agent:test",
                parent_id=WorkItemId("does-not-exist"),
            )


def test_create_within_accepts_an_existing_repository_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)
    repository_node_type = workspace.node_types[0]
    repository_node = Node(
        workspace_id=workspace.id, node_type_id=repository_node_type.id, title="apilex-agent"
    )
    SqliteNodeRepository(sqlite_connection).save(repository_node)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        epic, _ = service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="Epic",
            source="agent:test",
            repository_node_id=repository_node.id,
        )

    assert epic.repository_node_id == repository_node.id


def test_create_within_rejects_a_nonexistent_repository_node(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    node_type = service.require_node_type(workspace)

    with pytest.raises(RepositoryNodeNotFoundError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=WorkItemKind.EPIC,
                work_type=WorkItemType.FEATURE,
                title="Epic",
                source="agent:test",
                repository_node_id=NodeId("does-not-exist"),
            )


def test_require_workspace_rejects_an_unknown_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, _ = _service(sqlite_connection)
    with pytest.raises(WorkspaceNotFoundError):
        service.require_workspace(WorkspaceId("does-not-exist"))


def test_require_node_type_rejects_a_workspace_without_the_work_item_role(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, _ = _service(sqlite_connection)
    bare_workspace = new_workspace("Bare")
    with pytest.raises(WorkItemNodeTypeMissingError):
        service.require_node_type(bare_workspace)


def _create_epic(
    service: WorkItemService, workspace: Workspace, sqlite_connection: sqlite3.Connection
):
    node_type = service.require_node_type(workspace)
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        return service.create_within(
            unit_of_work,
            workspace,
            node_type,
            kind=WorkItemKind.EPIC,
            work_type=WorkItemType.FEATURE,
            title="Epic",
            source="agent:test",
        )


def _create_document(sqlite_connection: sqlite3.Connection, workspace: Workspace) -> Document:
    document = Document(
        workspace_id=workspace.id, kind=DocumentKind.NOTE, title="A note", source="agent:test"
    )
    SqliteDocumentRepository(sqlite_connection).save(document)
    return document


def test_attach_document_creates_a_node_link(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        link = service.attach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )

    assert link.target_type is DocumentLinkTargetType.NODE
    assert link.target_id == epic.node_id
    links = SqliteResearchUnitOfWork(sqlite_connection).document_links.list_by_document(document.id)
    assert len(links) == 1


def test_attach_document_is_idempotent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        first = service.attach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        second = service.attach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )

    assert first.id == second.id
    links = SqliteResearchUnitOfWork(sqlite_connection).document_links.list_by_document(document.id)
    assert len(links) == 1


def test_detach_document_removes_the_link(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        service.attach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        service.detach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )

    links = SqliteResearchUnitOfWork(sqlite_connection).document_links.list_by_document(document.id)
    assert links == ()


def test_detach_document_is_a_no_op_when_never_linked(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        service.detach_document_within(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )


def test_attach_document_rejects_a_document_from_a_different_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    other_workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Other")))
    SqliteWorkspaceRepository(sqlite_connection).save(other_workspace)
    document = _create_document(sqlite_connection, other_workspace)

    with pytest.raises(WorkItemDocumentNotFoundError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.attach_document_within(
                unit_of_work,
                workspace_id=workspace.id,
                work_item_id=epic.id,
                document_id=document.id,
            )


def test_attach_document_rejects_a_nonexistent_work_item(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    with pytest.raises(WorkItemNotFoundError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.attach_document_within(
                unit_of_work,
                workspace_id=workspace.id,
                work_item_id=WorkItemId("does-not-exist"),
                document_id=document.id,
            )


def test_attach_document_rejects_a_nonexistent_document(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)

    with pytest.raises(WorkItemDocumentNotFoundError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            service.attach_document_within(
                unit_of_work,
                workspace_id=workspace.id,
                work_item_id=epic.id,
                document_id=DocumentId("does-not-exist"),
            )


def test_create_and_list_workspace_return_the_workspace_items(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="An epic",
        body="Body",
        source="manual",
    )

    assert service.list_workspace(workspace.id) == (epic,)


def test_get_by_node_returns_the_backing_work_item(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, node = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )

    assert service.get_by_node(node.id) == epic


def test_get_by_node_raises_for_an_unbacked_node(sqlite_connection: sqlite3.Connection) -> None:
    service, _ = _service(sqlite_connection)
    with pytest.raises(WorkItemNotFoundError):
        service.get_by_node(NodeId("does-not-exist"))


def test_create_rejects_an_illegal_parent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    task, _ = service.create(
        workspace.id,
        kind=WorkItemKind.TASK,
        work_type=WorkItemType.FEATURE,
        title="Task",
        source="manual",
    )
    with pytest.raises(InvalidWorkItemParentError):
        service.create(
            workspace.id,
            kind=WorkItemKind.STORY,
            work_type=WorkItemType.FEATURE,
            title="Story under a task",
            source="manual",
            parent_id=task.id,
        )


def test_update_sets_planning_fields_and_keeps_others(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )

    updated = service.update(
        workspace.id,
        epic.id,
        WorkItemUpdatePatch(
            status=WorkItemStatus.IN_PROGRESS,
            work_type=WorkItemType.RESEARCH,
            priority=WorkItemPriority.HIGH,
            assignee="Ada",
            progress_percent=35,
        ),
    )

    assert updated.status is WorkItemStatus.IN_PROGRESS
    assert updated.work_type is WorkItemType.RESEARCH
    assert updated.priority is WorkItemPriority.HIGH
    assert updated.assignee == "Ada"
    assert updated.progress_percent == 35
    assert service.get(epic.id).kind is WorkItemKind.EPIC


def test_update_clear_flags_reset_nullable_fields(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    service.update(
        workspace.id,
        epic.id,
        WorkItemUpdatePatch(
            priority=WorkItemPriority.LOW, due_date=date(2026, 9, 1), assignee="Ada"
        ),
    )

    cleared = service.update(
        workspace.id,
        epic.id,
        WorkItemUpdatePatch(clear_priority=True, clear_due_date=True, clear_assignee=True),
    )

    assert cleared.priority is None
    assert cleared.due_date is None
    assert cleared.assignee is None


def test_update_rejects_an_out_of_range_progress(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    with pytest.raises(InvariantViolationError):
        service.update(
            workspace.id,
            epic.id,
            WorkItemUpdatePatch(progress_percent=101),
        )


def test_update_validates_a_new_repository_node(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    with pytest.raises(RepositoryNodeNotFoundError):
        service.update(
            workspace.id,
            epic.id,
            WorkItemUpdatePatch(repository_node_id=NodeId("does-not-exist")),
        )


def test_update_rejects_a_work_item_from_another_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    other = ensure_semantic_schema(seed_default_schema(new_workspace("Other")))
    SqliteWorkspaceRepository(sqlite_connection).save(other)
    epic, _ = service.create(
        other.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic in other",
        source="manual",
    )

    with pytest.raises(WorkItemNotFoundError):
        service.update(
            workspace.id,
            epic.id,
            WorkItemUpdatePatch(status=WorkItemStatus.DONE),
        )


def test_add_and_list_checklist_items_append_in_position_order(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )

    first = service.add_checklist_item(workspace.id, epic.id, "Scope the corpus")
    second = service.add_checklist_item(workspace.id, epic.id, "Draft the plan")

    items = service.list_checklist_items(epic.id)
    assert [item.id for item in items] == [first.id, second.id]
    assert [item.position for item in items] == [0, 1]
    assert all(item.is_completed is False for item in items)


def test_add_checklist_item_rejects_an_item_for_another_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    other = ensure_semantic_schema(seed_default_schema(new_workspace("Other")))
    SqliteWorkspaceRepository(sqlite_connection).save(other)
    epic, _ = service.create(
        other.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic in other",
        source="manual",
    )

    with pytest.raises(WorkItemNotFoundError):
        service.add_checklist_item(workspace.id, epic.id, "Nope")


def test_update_checklist_item_edits_label_and_completion(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    item = service.add_checklist_item(workspace.id, epic.id, "Scope")

    toggled = service.update_checklist_item(workspace.id, item.id, is_completed=True)
    renamed = service.update_checklist_item(workspace.id, item.id, label="Scope the corpus")

    assert toggled.is_completed is True
    assert renamed.label == "Scope the corpus"
    assert renamed.is_completed is True


def test_update_checklist_item_rejects_an_unknown_item(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    with pytest.raises(WorkItemChecklistItemNotFoundError):
        service.update_checklist_item(
            workspace.id, WorkItemChecklistItemId("does-not-exist"), is_completed=True
        )


def test_remove_checklist_item_renumbers_the_remaining_items(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    first = service.add_checklist_item(workspace.id, epic.id, "A")
    second = service.add_checklist_item(workspace.id, epic.id, "B")
    third = service.add_checklist_item(workspace.id, epic.id, "C")

    service.remove_checklist_item(workspace.id, first.id)

    remaining = service.list_checklist_items(epic.id)
    assert [item.id for item in remaining] == [second.id, third.id]
    assert [item.position for item in remaining] == [0, 1]


def test_reorder_checklist_items_applies_the_given_dense_order(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    first = service.add_checklist_item(workspace.id, epic.id, "A")
    second = service.add_checklist_item(workspace.id, epic.id, "B")
    third = service.add_checklist_item(workspace.id, epic.id, "C")

    reordered = service.reorder_checklist_items(
        workspace.id, epic.id, (third.id, first.id, second.id)
    )

    assert [item.id for item in reordered] == [third.id, first.id, second.id]
    assert [item.position for item in reordered] == [0, 1, 2]


def test_reorder_checklist_items_rejects_a_partial_or_mismatched_id_set(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Epic",
        source="manual",
    )
    first = service.add_checklist_item(workspace.id, epic.id, "A")
    service.add_checklist_item(workspace.id, epic.id, "B")

    with pytest.raises(InvalidChecklistOrderError):
        service.reorder_checklist_items(workspace.id, epic.id, (first.id,))
    with pytest.raises(InvalidChecklistOrderError):
        service.reorder_checklist_items(
            workspace.id,
            epic.id,
            (first.id, WorkItemChecklistItemId("does-not-exist")),
        )


def test_attach_and_list_linked_documents_round_trip(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    link = service.attach_document(
        workspace_id=workspace.id,
        work_item_id=epic.id,
        document_id=document.id,
    )

    assert link.target_type is DocumentLinkTargetType.NODE
    assert service.list_linked_documents(epic.id) == (link,)


def test_detach_via_public_wrapper_is_idempotent(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace = _service(sqlite_connection)
    epic, _ = _create_epic(service, workspace, sqlite_connection)
    document = _create_document(sqlite_connection, workspace)

    service.attach_document(
        workspace_id=workspace.id, work_item_id=epic.id, document_id=document.id
    )
    service.detach_document(
        workspace_id=workspace.id, work_item_id=epic.id, document_id=document.id
    )
    service.detach_document(
        workspace_id=workspace.id, work_item_id=epic.id, document_id=document.id
    )

    assert service.list_linked_documents(epic.id) == ()


def test_rest_created_work_item_is_search_indexed(sqlite_connection: sqlite3.Connection) -> None:
    """S9-F03 search parity: the REST/UI create path must index the work item's node text just
    like the MCP path, so a Tasks-tab work item is searchable."""
    workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Personal")))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    search_index = SqliteSearchIndexRepository(sqlite_connection)
    service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        search_index=search_index,
    )
    search_service = SearchService(
        SqliteNodeRepository(sqlite_connection),
        SqliteResourceRepository(sqlite_connection),
        SqliteDocumentRepository(sqlite_connection),
        SqliteBm25SearchEngine(sqlite_connection),
    )

    created, _ = service.create(
        workspace.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        title="Searchable epic",
        body="A distinctive searchable phrase for the corpus refresh",
        source="manual",
    )

    hits = search_service.search(workspace.id, "distinctive searchable phrase").results
    assert any(hit.node is not None and hit.node.title == "Searchable epic" for hit in hits)

    service.delete(workspace.id, created.id)

    hits = search_service.search(workspace.id, "distinctive searchable phrase").results
    assert not any(hit.node is not None and hit.node.title == "Searchable epic" for hit in hits)
