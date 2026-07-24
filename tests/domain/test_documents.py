from __future__ import annotations

from typing import Any

import pytest

from personal_graph_os.domain.documents import (
    Collection,
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
    DocumentVersion,
    Tag,
)
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    CollectionId,
    DocumentId,
    TagId,
    WorkspaceId,
    new_id,
)


def _document(**overrides: Any) -> Document:
    defaults: dict[str, Any] = {
        "workspace_id": WorkspaceId(new_id()),
        "kind": DocumentKind.NOTE,
        "title": "A standalone note",
        "source": "manual",
    }
    defaults.update(overrides)
    return Document(**defaults)


def test_document_requires_no_graph_node() -> None:
    """EP-2026-012 decision: a Document is valid without ever referencing a Node."""
    document = _document()
    assert document.id is not None
    assert not hasattr(document, "node_id")


def test_document_rejects_empty_title() -> None:
    with pytest.raises(InvariantViolationError):
        _document(title="   ")


def test_document_rejects_empty_source() -> None:
    with pytest.raises(InvariantViolationError):
        _document(source="")


def test_document_rejects_duplicate_tag_ids() -> None:
    tag_id = TagId(new_id())
    with pytest.raises(InvariantViolationError):
        _document(tag_ids=(tag_id, tag_id))


def test_collection_rejects_being_its_own_parent() -> None:
    collection_id = CollectionId(new_id())
    with pytest.raises(InvariantViolationError):
        Collection(
            id=collection_id,
            workspace_id=WorkspaceId(new_id()),
            name="Loop",
            parent_id=collection_id,
        )


def test_collection_nests_under_a_different_parent() -> None:
    parent = Collection(workspace_id=WorkspaceId(new_id()), name="Parent")
    child = Collection(workspace_id=parent.workspace_id, name="Child", parent_id=parent.id)
    assert child.parent_id == parent.id


def test_tag_rejects_empty_name() -> None:
    with pytest.raises(InvariantViolationError):
        Tag(workspace_id=WorkspaceId(new_id()), name=" ")


def test_document_version_requires_version_number_at_least_one() -> None:
    with pytest.raises(InvariantViolationError):
        DocumentVersion(
            document_id=DocumentId(new_id()),
            version_number=0,
            body_markdown="# hi",
            created_by="agent:claude",
        )


def test_document_version_rejects_empty_created_by() -> None:
    with pytest.raises(InvariantViolationError):
        DocumentVersion(
            document_id=DocumentId(new_id()),
            version_number=1,
            body_markdown="# hi",
            created_by=" ",
        )


def test_document_link_rejects_linking_a_document_to_itself() -> None:
    document_id = DocumentId(new_id())
    with pytest.raises(InvariantViolationError):
        DocumentLink(
            document_id=document_id,
            target_type=DocumentLinkTargetType.DOCUMENT,
            target_id=document_id,
        )


def test_document_link_to_a_node_is_valid() -> None:
    link = DocumentLink(
        document_id=DocumentId(new_id()),
        target_type=DocumentLinkTargetType.NODE,
        target_id=new_id(),
    )
    assert link.target_type is DocumentLinkTargetType.NODE
