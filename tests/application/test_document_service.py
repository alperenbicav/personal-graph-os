from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.document_service import (
    CollectionNotFoundError,
    DocumentLinkTargetNotFoundError,
    DocumentNotFoundError,
    DocumentService,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.documents import DocumentKind, DocumentLinkTargetType
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import DocumentId, WorkspaceId
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteCollectionRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteNodeRepository,
    SqliteTagRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _setup(sqlite_connection: sqlite3.Connection) -> tuple[DocumentService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    service = DocumentService(
        workspace_repository,
        SqliteDocumentRepository(sqlite_connection),
        SqliteDocumentVersionRepository(sqlite_connection),
        SqliteDocumentLinkRepository(sqlite_connection),
        SqliteCollectionRepository(sqlite_connection),
        SqliteTagRepository(sqlite_connection),
        SqliteNodeRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return service, workspace.id


def test_create_document_persists_title_kind_and_first_version(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)

    document, version = service.create_document(
        workspace_id,
        title="My first note",
        kind=DocumentKind.NOTE,
        source="manual",
        body_markdown="hello world",
    )

    assert service.get(document.id).title == "My first note"
    assert version.version_number == 1
    assert version.body_markdown == "hello world"
    assert service.get_body(document.id).id == version.id


def test_create_document_resolves_tag_names_idempotently(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)

    document, _ = service.create_document(
        workspace_id,
        title="Tagged note",
        kind=DocumentKind.NOTE,
        source="manual",
        tag_names=("alpha", "beta", "alpha"),
    )

    tags = {tag.id: tag.name for tag in service.list_tags(workspace_id)}
    assert len(document.tag_ids) == 2
    assert {tags[tag_id] for tag_id in document.tag_ids} == {"alpha", "beta"}

    # Reusing the same tag name across a second document must not create a duplicate tag.
    service.create_document(
        workspace_id, title="Second note", kind=DocumentKind.NOTE, source="manual",
        tag_names=("alpha",),
    )
    assert len(service.list_tags(workspace_id)) == 2


def test_create_document_rejects_unknown_collection(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace_id = _setup(sqlite_connection)

    with pytest.raises(CollectionNotFoundError):
        service.create_document(
            workspace_id,
            title="Orphan",
            kind=DocumentKind.NOTE,
            source="manual",
            collection_id="does-not-exist",  # type: ignore[arg-type]
        )


def test_update_metadata_reruns_validators_and_records_change(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)
    document, _ = service.create_document(
        workspace_id, title="Draft", kind=DocumentKind.NOTE, source="manual"
    )

    updated = service.update_metadata(document.id, title="Final title", is_archived=True)

    assert updated.title == "Final title"
    assert updated.is_archived is True
    with pytest.raises(InvariantViolationError):
        service.update_metadata(document.id, title="   ")


def test_edit_body_appends_immutable_version_without_overwriting(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)
    document, first_version = service.create_document(
        workspace_id,
        title="Evolving note",
        kind=DocumentKind.NOTE,
        source="manual",
        body_markdown="v1",
    )

    second_version = service.edit_body(document.id, body_markdown="v2", actor="human/local-user")

    assert second_version.version_number == first_version.version_number + 1
    versions = service.list_versions(document.id)
    assert [v.body_markdown for v in versions] == ["v1", "v2"]
    assert service.get_body(document.id).body_markdown == "v2"


def test_backlinks_return_documents_linking_to_this_document(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)
    target, _ = service.create_document(
        workspace_id, title="Target", kind=DocumentKind.NOTE, source="manual"
    )
    source, _ = service.create_document(
        workspace_id, title="Source", kind=DocumentKind.NOTE, source="manual"
    )

    service.add_link(source.id, DocumentLinkTargetType.DOCUMENT, target.id)

    backlinks = service.list_backlinks(target.id)
    assert [doc.id for doc in backlinks] == [source.id]
    assert service.list_backlinks(source.id) == ()


def test_add_link_rejects_missing_node_target(sqlite_connection: sqlite3.Connection) -> None:
    service, workspace_id = _setup(sqlite_connection)
    document, _ = service.create_document(
        workspace_id, title="Note", kind=DocumentKind.NOTE, source="manual"
    )

    with pytest.raises(DocumentLinkTargetNotFoundError):
        service.add_link(document.id, DocumentLinkTargetType.NODE, "missing-node")


def test_get_detail_aggregates_collection_tags_links_and_backlinks(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)
    collection = service.create_collection(workspace_id, "Research notes")
    workspace = SqliteWorkspaceRepository(sqlite_connection).get(workspace_id)
    assert workspace is not None
    node = Node(
        workspace_id=workspace_id, node_type_id=workspace.node_types[0].id, title="A node"
    )
    SqliteNodeRepository(sqlite_connection).save(node)

    document, _ = service.create_document(
        workspace_id,
        title="Deep dive",
        kind=DocumentKind.NOTE,
        source="manual",
        collection_id=collection.id,
        tag_names=("wiki",),
        link_targets=((DocumentLinkTargetType.NODE, node.id),),
    )
    other, _ = service.create_document(
        workspace_id, title="Referencing note", kind=DocumentKind.NOTE, source="manual"
    )
    service.add_link(other.id, DocumentLinkTargetType.DOCUMENT, document.id)

    detail = service.get_detail(document.id)

    assert detail.collection is not None and detail.collection.id == collection.id
    assert {tag.name for tag in detail.tags} == {"wiki"}
    assert len(detail.outbound_links) == 1
    assert detail.outbound_links[0].target_id == node.id
    assert [doc.id for doc in detail.backlinks] == [other.id]
    assert detail.version_count == 1


def test_upsert_for_agent_within_creates_then_updates_same_document(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, workspace_id = _setup(sqlite_connection)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        document, version, was_created = service.upsert_for_agent_within(
            unit_of_work,
            workspace_id,
            title="Agent note",
            body_markdown="draft",
            actor="agent:test",
        )
    assert was_created is True
    assert version.version_number == 1

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        updated_document, updated_version, was_created_again = service.upsert_for_agent_within(
            unit_of_work,
            workspace_id,
            title="Agent note",
            body_markdown="final",
            actor="agent:test",
        )
    assert was_created_again is False
    assert updated_document.id == document.id
    assert updated_version.version_number == 2
    assert service.get_body(document.id).body_markdown == "final"


def test_get_raises_when_document_missing(sqlite_connection: sqlite3.Connection) -> None:
    service, _ = _setup(sqlite_connection)
    with pytest.raises(DocumentNotFoundError):
        service.get(DocumentId("missing"))
