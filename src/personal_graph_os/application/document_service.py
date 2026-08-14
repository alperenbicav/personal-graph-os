"""The Wiki's independent Markdown knowledge store (EP-2026-012 ST-07): create/update a
`Document`'s metadata, append immutable `DocumentVersion` revisions, manage its `Collection`
and `Tag` taxonomy, and maintain its optional typed `DocumentLink`s to graph nodes or other
documents.

A `Document` never requires a graph node (ST-01 decision); every write here stays inside the
standalone document aggregate and only ever touches a `Node` indirectly, by validating that a
`DocumentLinkTargetType.NODE` target actually exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from personal_graph_os.application.activity_recording import MutationContext, record_activity_event
from personal_graph_os.application.repositories import (
    CollectionRepository,
    DocumentLinkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    NodeRepository,
    SearchIndexRepository,
    TagRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.domain.activity import MutationAction
from personal_graph_os.domain.documents import (
    Collection,
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
    DocumentVersion,
    Tag,
)
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.identifiers import (
    CollectionId,
    DocumentId,
    DocumentLinkId,
    NodeId,
    TagId,
    WorkspaceId,
)
from personal_graph_os.domain.search import SearchEntityType, SearchScope


class DocumentNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a document that does not exist."""


class CollectionNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a collection that does not exist."""


class TagNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a tag that does not exist."""


class DocumentLinkNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a document link that does not exist."""


class DocumentLinkTargetNotFoundError(UnknownSchemaReferenceError):
    """Raised when a `DocumentLink`'s node/document target does not exist."""


@dataclass(frozen=True)
class DocumentDetail:
    """Everything the Wiki's metadata/relations inspector needs in one round trip: the
    document, its latest body, its collection/tags, its outbound links, and its backlinks."""

    document: Document
    latest_version: DocumentVersion
    collection: Collection | None
    tags: tuple[Tag, ...]
    outbound_links: tuple[DocumentLink, ...]
    backlinks: tuple[Document, ...]
    version_count: int


class DocumentService:
    """The only path through which Wiki documents, collections, tags, versions, and links are
    created and mutated -- REST and, for `upsert_for_agent`, MCP alike."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        documents: DocumentRepository,
        document_versions: DocumentVersionRepository,
        document_links: DocumentLinkRepository,
        collections: CollectionRepository,
        tags: TagRepository,
        nodes: NodeRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._documents = documents
        self._document_versions = document_versions
        self._document_links = document_links
        self._collections = collections
        self._tags = tags
        self._nodes = nodes
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    # -- reads ---------------------------------------------------------------------------

    def get(self, document_id: DocumentId) -> Document:
        return self._require_document(document_id)

    def list_documents(
        self,
        workspace_id: WorkspaceId,
        *,
        collection_id: CollectionId | None = None,
        include_archived: bool = False,
    ) -> tuple[Document, ...]:
        self._require_workspace(workspace_id)
        if collection_id is not None:
            return self._documents.list_by_collection(collection_id)
        return self._documents.list_by_workspace(workspace_id, include_archived=include_archived)

    def list_versions(self, document_id: DocumentId) -> tuple[DocumentVersion, ...]:
        self._require_document(document_id)
        return self._document_versions.list_by_document(document_id)

    def get_body(self, document_id: DocumentId) -> DocumentVersion:
        self._require_document(document_id)
        return self._require_latest_version(document_id)

    def list_outbound_links(self, document_id: DocumentId) -> tuple[DocumentLink, ...]:
        self._require_document(document_id)
        return self._document_links.list_by_document(document_id)

    def list_backlinks(self, document_id: DocumentId) -> tuple[Document, ...]:
        """Every other document with a `DocumentLink` pointing at `document_id`."""
        self._require_document(document_id)
        links = self._document_links.list_by_target(DocumentLinkTargetType.DOCUMENT, document_id)
        backlinked: list[Document] = []
        for link in links:
            source = self._documents.get(link.document_id)
            if source is not None:
                backlinked.append(source)
        return tuple(backlinked)

    def get_detail(self, document_id: DocumentId) -> DocumentDetail:
        document = self._require_document(document_id)
        latest_version = self._require_latest_version(document_id)
        collection = (
            self._collections.get(document.collection_id)
            if document.collection_id is not None
            else None
        )
        tags = tuple(tag for tag in (self._tags.get(tag_id) for tag_id in document.tag_ids) if tag)
        return DocumentDetail(
            document=document,
            latest_version=latest_version,
            collection=collection,
            tags=tags,
            outbound_links=self._document_links.list_by_document(document_id),
            backlinks=self.list_backlinks(document_id),
            version_count=len(self._document_versions.list_by_document(document_id)),
        )

    def list_collections(self, workspace_id: WorkspaceId) -> tuple[Collection, ...]:
        self._require_workspace(workspace_id)
        return self._collections.list_by_workspace(workspace_id)

    def list_tags(self, workspace_id: WorkspaceId) -> tuple[Tag, ...]:
        self._require_workspace(workspace_id)
        return self._tags.list_by_workspace(workspace_id)

    # -- collections/tags ------------------------------------------------------------------

    def create_collection(
        self, workspace_id: WorkspaceId, name: str, *, parent_id: CollectionId | None = None
    ) -> Collection:
        self._require_workspace(workspace_id)
        if parent_id is not None:
            self._require_collection(parent_id)
        collection = Collection(workspace_id=workspace_id, name=name, parent_id=parent_id)
        self._collections.save(collection)
        return collection

    def get_or_create_tag(self, workspace_id: WorkspaceId, name: str) -> Tag:
        """Idempotent by `(workspace_id, name)` (ST-01 invariant): callers pass tag names, not
        ids, so a repeated name is always the same reusable label instead of a duplicate."""
        self._require_workspace(workspace_id)
        existing = self._tags.get_by_name(workspace_id, name)
        if existing is not None:
            return existing
        tag = Tag(workspace_id=workspace_id, name=name)
        self._tags.save(tag)
        return tag

    def _resolve_tag_ids(
        self, workspace_id: WorkspaceId, tag_names: tuple[str, ...]
    ) -> tuple[TagId, ...]:
        return tuple(
            self.get_or_create_tag(workspace_id, name).id for name in dict.fromkeys(tag_names)
        )

    # -- documents ---------------------------------------------------------------------------

    def create_document(
        self,
        workspace_id: WorkspaceId,
        *,
        title: str,
        kind: DocumentKind,
        source: str,
        body_markdown: str = "",
        collection_id: CollectionId | None = None,
        tag_names: tuple[str, ...] = (),
        link_targets: tuple[tuple[DocumentLinkTargetType, str], ...] = (),
    ) -> tuple[Document, DocumentVersion]:
        self._require_workspace(workspace_id)
        if collection_id is not None:
            self._require_collection(collection_id)
        tag_ids = self._resolve_tag_ids(workspace_id, tag_names)
        for target_type, target_id in link_targets:
            self._require_link_target(target_type, target_id)

        with self._unit_of_work_factory() as unit_of_work:
            document = Document(
                workspace_id=workspace_id,
                kind=kind,
                title=title,
                collection_id=collection_id,
                tag_ids=tag_ids,
                source=source,
            )
            version = DocumentVersion(
                document_id=document.id,
                version_number=1,
                body_markdown=body_markdown,
                created_by=source,
            )
            unit_of_work.documents.save_without_commit(document)
            unit_of_work.document_versions.save_without_commit(version)
            for target_type, target_id in link_targets:
                unit_of_work.document_links.save_without_commit(
                    DocumentLink(
                        document_id=document.id, target_type=target_type, target_id=target_id
                    )
                )
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type="document",
                entity_id=document.id,
                action=MutationAction.CREATED,
                after_state=document.model_dump(mode="json"),
            )
        self.index_document_for_search(document.id)
        return document, version

    def update_metadata(
        self,
        document_id: DocumentId,
        *,
        title: str | None = None,
        kind: DocumentKind | None = None,
        collection_id: CollectionId | None = None,
        clear_collection: bool = False,
        tag_names: tuple[str, ...] | None = None,
        is_archived: bool | None = None,
    ) -> Document:
        document = self._require_document(document_id)
        if clear_collection and collection_id is not None:
            raise UnknownSchemaReferenceError(
                "collection_id and clear_collection cannot both be set"
            )
        next_collection_id = document.collection_id
        if clear_collection:
            next_collection_id = None
        elif collection_id is not None:
            self._require_collection(collection_id)
            next_collection_id = collection_id
        next_tag_ids = document.tag_ids
        if tag_names is not None:
            next_tag_ids = self._resolve_tag_ids(document.workspace_id, tag_names)

        before = document.model_dump(mode="json")
        # Reconstruct through the constructor (not `model_copy`) so every field validator,
        # including the title/source non-empty and unique-tag-ids checks, reruns.
        updated = Document(
            id=document.id,
            workspace_id=document.workspace_id,
            kind=kind if kind is not None else document.kind,
            title=title if title is not None else document.title,
            collection_id=next_collection_id,
            tag_ids=next_tag_ids,
            is_archived=is_archived if is_archived is not None else document.is_archived,
            source=document.source,
            source_reference=document.source_reference,
            created_at=document.created_at,
            updated_at=datetime.now(UTC),
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.documents.save_without_commit(updated)
            record_activity_event(
                unit_of_work,
                workspace_id=updated.workspace_id,
                context=MutationContext.rest(),
                entity_type="document",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=before,
                after_state=updated.model_dump(mode="json"),
            )
        self.index_document_for_search(updated.id)
        return updated

    def archive(self, document_id: DocumentId) -> Document:
        """Soft-archive a document (default, recoverable lifecycle; ST-09)."""
        return self.update_metadata(document_id, is_archived=True)

    def restore(self, document_id: DocumentId) -> Document:
        """Clear a document's archive flag (ST-09)."""
        return self.update_metadata(document_id, is_archived=False)

    def update_metadata_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        document_id: DocumentId,
        *,
        is_archived: bool,
    ) -> Document:
        """Same write as `update_metadata(is_archived=...)`, into a caller-managed, already-open
        `unit_of_work` (used by the MCP gateway so the activity event and idempotency receipt
        commit atomically with the mutation)."""
        document = unit_of_work.documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError(f"document {document_id} does not exist")
        if document.is_archived == is_archived:
            return document
        updated = document.model_copy(
            update={"is_archived": is_archived, "updated_at": datetime.now(UTC)}
        )
        unit_of_work.documents.save_without_commit(updated)
        return updated

    def delete(self, document_id: DocumentId) -> None:
        """Hard-delete a document; its versions, links, and tag rows cascade (ST-09).
        Irrecoverable; callers must gate this behind an explicit destructive confirmation."""
        self._require_document(document_id)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.documents.delete_without_commit(document_id)
        self.remove_document_from_search(document_id)

    def edit_body(
        self, document_id: DocumentId, *, body_markdown: str, actor: str
    ) -> DocumentVersion:
        """Append a new immutable `DocumentVersion`; the document's prior body is never
        overwritten in place (ST-01 invariant)."""
        document = self._require_document(document_id)
        latest = self._require_latest_version(document_id)
        next_version = DocumentVersion(
            document_id=document_id,
            version_number=latest.version_number + 1,
            body_markdown=body_markdown,
            created_by=actor,
        )
        touched = Document(
            id=document.id,
            workspace_id=document.workspace_id,
            kind=document.kind,
            title=document.title,
            collection_id=document.collection_id,
            tag_ids=document.tag_ids,
            is_archived=document.is_archived,
            source=document.source,
            source_reference=document.source_reference,
            created_at=document.created_at,
            updated_at=datetime.now(UTC),
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.document_versions.save_without_commit(next_version)
            unit_of_work.documents.save_without_commit(touched)
            record_activity_event(
                unit_of_work,
                workspace_id=document.workspace_id,
                context=MutationContext.rest(),
                entity_type="document",
                entity_id=document.id,
                action=MutationAction.UPDATED,
                before_state={"version_number": latest.version_number},
                after_state={"version_number": next_version.version_number},
            )
        self.index_document_for_search(document.id)
        return next_version

    def add_link(
        self, document_id: DocumentId, target_type: DocumentLinkTargetType, target_id: str
    ) -> DocumentLink:
        document = self._require_document(document_id)
        self._require_link_target(target_type, target_id)
        link = DocumentLink(document_id=document_id, target_type=target_type, target_id=target_id)
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.document_links.save_without_commit(link)
            record_activity_event(
                unit_of_work,
                workspace_id=document.workspace_id,
                context=MutationContext.rest(),
                entity_type="document_link",
                entity_id=link.id,
                action=MutationAction.CREATED,
                after_state=link.model_dump(mode="json"),
            )
        return link

    def remove_link(self, document_link_id: DocumentLinkId) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.document_links.delete_without_commit(document_link_id)

    # -- agent upsert (MCP-facing) -----------------------------------------------------------

    def upsert_for_agent_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        *,
        title: str,
        body_markdown: str,
        actor: str,
        kind: DocumentKind = DocumentKind.NOTE,
        document_id: DocumentId | None = None,
        collection_id: CollectionId | None = None,
        tag_names: tuple[str, ...] = (),
    ) -> tuple[Document, DocumentVersion, bool]:
        """Same write path an MCP agent tool uses: update the referenced document when
        `document_id` is given, or reuse an exact `(workspace_id, title)` match, or create a
        new document -- always inside the caller-managed `unit_of_work` so the write and its
        `ActivityEvent` commit or roll back together. Returns `(document, version, was_created)`.
        """
        tag_ids = self._resolve_tag_ids(workspace_id, tag_names) if tag_names else None
        existing = (
            self._require_document(document_id)
            if document_id is not None
            else next(
                (
                    candidate
                    for candidate in self._documents.list_by_workspace(
                        workspace_id, include_archived=True
                    )
                    if candidate.title == title
                ),
                None,
            )
        )
        if existing is not None:
            latest = self._require_latest_version(existing.id)
            next_version = DocumentVersion(
                document_id=existing.id,
                version_number=latest.version_number + 1,
                body_markdown=body_markdown,
                created_by=actor,
            )
            updated = Document(
                id=existing.id,
                workspace_id=existing.workspace_id,
                kind=kind,
                title=existing.title,
                collection_id=(
                    collection_id if collection_id is not None else existing.collection_id
                ),
                tag_ids=tag_ids if tag_ids is not None else existing.tag_ids,
                is_archived=existing.is_archived,
                source=existing.source,
                source_reference=existing.source_reference,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )
            unit_of_work.documents.save_without_commit(updated)
            unit_of_work.document_versions.save_without_commit(next_version)
            return updated, next_version, False

        document = Document(
            workspace_id=workspace_id,
            kind=kind,
            title=title,
            collection_id=collection_id,
            tag_ids=tag_ids or (),
            source=actor,
        )
        version = DocumentVersion(
            document_id=document.id, version_number=1, body_markdown=body_markdown, created_by=actor
        )
        unit_of_work.documents.save_without_commit(document)
        unit_of_work.document_versions.save_without_commit(version)
        return document, version, True

    # -- helpers -----------------------------------------------------------------------------

    def _require_workspace(self, workspace_id: WorkspaceId) -> None:
        if self._workspaces.get(workspace_id) is None:
            raise UnknownSchemaReferenceError(f"workspace {workspace_id} does not exist")

    def _require_document(self, document_id: DocumentId) -> Document:
        document = self._documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError(f"document {document_id} does not exist")
        return document

    def _require_latest_version(self, document_id: DocumentId) -> DocumentVersion:
        version = self._document_versions.latest_for_document(document_id)
        if version is None:
            raise DocumentNotFoundError(f"document {document_id} has no version yet")
        return version

    def index_document_for_search(self, document_id: DocumentId) -> None:
        """(Re)index a document's title + latest body into the `wiki` search scope (ST-12).

        Safe to call after the writing unit of work has committed (`index_document` owns its own
        connection transaction), including from the MCP gateway after its `upsert_for_agent_within`
        unit of work exits.
        """
        if self._search_index is None:
            return
        document = self._documents.get(document_id)
        if document is None:
            return
        version = self._document_versions.latest_for_document(document_id)
        if version is None:
            return
        self._search_index.index_document(
            workspace_id=document.workspace_id,
            entity_type=SearchEntityType.DOCUMENT,
            entity_id=document.id,
            text=f"{document.title}\n{version.body_markdown}",
            scope=SearchScope.WIKI,
        )

    def remove_document_from_search(self, document_id: DocumentId) -> None:
        """Remove a document's `wiki` search row (ST-12): called on hard delete."""
        if self._search_index is None:
            return
        self._search_index.remove_document(
            entity_type=SearchEntityType.DOCUMENT, entity_id=document_id
        )

    def _require_collection(self, collection_id: CollectionId) -> Collection:
        collection = self._collections.get(collection_id)
        if collection is None:
            raise CollectionNotFoundError(f"collection {collection_id} does not exist")
        return collection

    def _require_link_target(self, target_type: DocumentLinkTargetType, target_id: str) -> None:
        if target_type is DocumentLinkTargetType.NODE:
            if self._nodes.get(NodeId(target_id)) is None:
                raise DocumentLinkTargetNotFoundError(f"node {target_id} does not exist")
        else:
            if self._documents.get(DocumentId(target_id)) is None:
                raise DocumentLinkTargetNotFoundError(f"document {target_id} does not exist")
