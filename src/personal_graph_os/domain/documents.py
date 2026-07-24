"""The independent Markdown knowledge store: `Document`, its `DocumentVersion` history,
`Collection` folders, reusable `Tag` labels, and optional `DocumentLink`s to graph/work objects.

A `Document` requires no `Node` and no graph projection (EP-2026-012 decision): a standalone
note, lesson, or generated plan is a first-class object on its own, and only ever gains a graph
relation through an explicit, optional `DocumentLink`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    CollectionId,
    DocumentId,
    DocumentLinkId,
    DocumentVersionId,
    TagId,
    WorkspaceId,
    new_id,
)


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class DocumentKind(StrEnum):
    NOTE = "note"
    LESSON = "lesson"
    DOCUMENTATION = "documentation"
    PLAN = "plan"


class DocumentLinkTargetType(StrEnum):
    """What a `DocumentLink` points at. `NODE` is the single canonical identity for every
    graph-projected object -- a Resource, WorkItem, or future Repository's own projection node
    -- so linking to a paper, repository, or work item is always represented the same one way
    (review finding R02): there is no separate per-domain target type to accidentally duplicate
    the same relation under two different target ids."""

    NODE = "node"
    DOCUMENT = "document"


class Collection(BaseModel):
    """A folder-like grouping for documents; collections may nest under one another."""

    id: CollectionId = Field(default_factory=lambda: CollectionId(new_id()))
    workspace_id: WorkspaceId
    name: str
    parent_id: CollectionId | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "Collection.name")

    def model_post_init(self, _context: object) -> None:
        if self.parent_id is not None and self.parent_id == self.id:
            raise InvariantViolationError(f"Collection {self.id} cannot be its own parent")


class Tag(BaseModel):
    """A reusable workspace-scoped label; a name is unique per workspace so callers can look
    one up idempotently instead of creating duplicates."""

    id: TagId = Field(default_factory=lambda: TagId(new_id()))
    workspace_id: WorkspaceId
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "Tag.name")


class Document(BaseModel):
    """Canonical identity and current metadata for one standalone Markdown document.

    The document's rendered content lives in its `DocumentVersion` history, not here, so every
    edit is versioned rather than overwritten in place.
    """

    id: DocumentId = Field(default_factory=lambda: DocumentId(new_id()))
    workspace_id: WorkspaceId
    kind: DocumentKind
    title: str
    collection_id: CollectionId | None = None
    tag_ids: tuple[TagId, ...] = ()
    is_archived: bool = False
    # Capture provenance: "manual", "agent:<identity>", "clickup", "telegram", etc. Never a
    # secret or raw transcript -- just the channel/actor identity that created this document.
    source: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("title", "source")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "Document field")
        return _non_empty(value, f"Document.{field_name}")

    @field_validator("tag_ids")
    @classmethod
    def _validate_unique_tag_ids(cls, value: tuple[TagId, ...]) -> tuple[TagId, ...]:
        if len(value) != len(set(value)):
            raise InvariantViolationError("Document.tag_ids must not contain duplicates")
        return value


class DocumentVersion(BaseModel):
    """One immutable, numbered revision of a `Document`'s Markdown body.

    `version_number` starts at 1 and increases by exactly 1 per document; a caller assembles the
    next version from the current one rather than this model inferring it, since only the
    repository knows the document's latest persisted version at write time.
    """

    id: DocumentVersionId = Field(default_factory=lambda: DocumentVersionId(new_id()))
    document_id: DocumentId
    version_number: int
    body_markdown: str
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_by")
    @classmethod
    def _validate_created_by(cls, value: str) -> str:
        return _non_empty(value, "DocumentVersion.created_by")

    @field_validator("version_number")
    @classmethod
    def _validate_version_number(cls, value: int) -> int:
        if value < 1:
            raise InvariantViolationError("DocumentVersion.version_number must be >= 1")
        return value


class DocumentLink(BaseModel):
    """An optional, typed relation from a `Document` to a graph node, work item, or another
    document. Never required for a document to exist or be valid on its own."""

    id: DocumentLinkId = Field(default_factory=lambda: DocumentLinkId(new_id()))
    document_id: DocumentId
    target_type: DocumentLinkTargetType
    target_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("target_id")
    @classmethod
    def _validate_target_id(cls, value: str) -> str:
        return _non_empty(value, "DocumentLink.target_id")

    def model_post_init(self, _context: object) -> None:
        if (
            self.target_type is DocumentLinkTargetType.DOCUMENT
            and self.target_id == self.document_id
        ):
            raise InvariantViolationError(
                f"DocumentLink {self.id} cannot link document {self.document_id} to itself"
            )
