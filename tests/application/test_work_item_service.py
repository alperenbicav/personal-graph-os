from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import WorkspaceNotFoundError, new_workspace
from personal_graph_os.application.work_item_service import (
    InvalidWorkItemParentError,
    RepositoryNodeNotFoundError,
    WorkItemDocumentNotFoundError,
    WorkItemNodeTypeMissingError,
    WorkItemNotFoundError,
    WorkItemService,
)
from personal_graph_os.domain.documents import Document, DocumentKind, DocumentLinkTargetType
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import DocumentId, NodeId, WorkItemId, WorkspaceId
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.work_items import WorkItemKind, WorkItemStatus, WorkItemType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteNodeRepository,
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
        link = service.attach_document(
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
        first = service.attach_document(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        second = service.attach_document(
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
        service.attach_document(
            unit_of_work,
            workspace_id=workspace.id,
            work_item_id=epic.id,
            document_id=document.id,
        )

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        service.detach_document(
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
        service.detach_document(
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
            service.attach_document(
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
            service.attach_document(
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
            service.attach_document(
                unit_of_work,
                workspace_id=workspace.id,
                work_item_id=epic.id,
                document_id=DocumentId("does-not-exist"),
            )
