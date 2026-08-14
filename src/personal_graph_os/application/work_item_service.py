"""Work item lifecycle service (EP-2026-012 ST-05): the single mutation boundary for Epic/Story/
Task work items and their backing `Node`, mirroring `ResourceService`'s node+aggregate pattern.

Enforces the hierarchy legality ST-01's final review flagged as an explicit residual risk for a
later mutation service: an Epic never has a parent (already enforced by `WorkItem` itself); a
Story's parent, if any, must be an Epic; a Task's parent, if any, must be a Story.
`repository_node_id`, if given, must reference an existing Node in the same workspace -- a
stable, typed reference (ST-01 review finding R01), never a free-text name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from personal_graph_os.application.repositories import (
    SearchIndexRepository,
    WorkItemChecklistItemRepository,
    WorkItemRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.semantic_keys import WORK_ITEM_NODE_TYPE_KEY
from personal_graph_os.application.services import WorkspaceNotFoundError
from personal_graph_os.domain.documents import DocumentLink, DocumentLinkTargetType
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import (
    DocumentId,
    NodeId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.schema import NodeType, Workspace
from personal_graph_os.domain.search import (
    SearchEntityType,
    SearchScope,
    build_node_search_text,
)
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemChecklistItem,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)


class WorkItemNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a work item that does not exist."""


class WorkItemNodeTypeMissingError(UnknownSchemaReferenceError):
    """Raised when a workspace has no node type carrying the 'work_item' semantic role yet.

    `ensure_semantic_schema()` guarantees this on every bootstrap, so this only fires if a
    caller constructs a `WorkItemService` against a workspace that skipped that step.
    """


class InvalidWorkItemParentError(UnknownSchemaReferenceError):
    """Raised when `parent_id` does not reference a work item of a legal parent kind for the
    child being created, does not exist, or belongs to a different workspace."""


class RepositoryNodeNotFoundError(UnknownSchemaReferenceError):
    """Raised when `repository_node_id` does not reference an existing Node in this
    workspace."""


class WorkItemDocumentNotFoundError(UnknownSchemaReferenceError):
    """Raised when `attach_document`/`detach_document` references a document that does not
    exist in the same workspace as the work item."""


class WorkItemChecklistItemNotFoundError(UnknownSchemaReferenceError):
    """Raised when a checklist-item operation references an item that does not exist."""


class InvalidChecklistOrderError(UnknownSchemaReferenceError):
    """Raised when a checklist reorder does not reference exactly the work item's current
    checklist item ids, once each."""


# An Epic's kind has no entry here: `WorkItem.model_post_init` already rejects any `parent_id`
# for it, so `_require_legal_parent` is never even reached for that case in practice.
_LEGAL_PARENT_KIND_BY_CHILD_KIND: dict[WorkItemKind, WorkItemKind] = {
    WorkItemKind.STORY: WorkItemKind.EPIC,
    WorkItemKind.TASK: WorkItemKind.STORY,
}


@dataclass(frozen=True)
class WorkItemUpdatePatch:
    """One typed mutation for `WorkItemService.update*` (ST-08).

    `None` means "leave the current value unchanged"; an explicit `clear_*` flag resets the
    corresponding nullable field to `None`. `progress_percent` is range-validated by the
    `WorkItem` model itself, and `repository_node_id` is validated against the workspace by the
    service before it is applied.
    """

    work_type: WorkItemType | None = None
    status: WorkItemStatus | None = None
    priority: WorkItemPriority | None = None
    due_date: date | None = None
    assignee: str | None = None
    blockers: str | None = None
    progress_percent: int | None = None
    repository_node_id: NodeId | None = None
    clear_priority: bool = False
    clear_due_date: bool = False
    clear_assignee: bool = False
    clear_blockers: bool = False
    clear_progress_percent: bool = False
    clear_repository_node_id: bool = False


class WorkItemService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        work_items: WorkItemRepository,
        work_item_checklist_items: WorkItemChecklistItemRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._work_items = work_items
        self._work_item_checklist_items = work_item_checklist_items
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    def require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def require_node_type(self, workspace: Workspace) -> NodeType:
        node_type = workspace.node_type_by_system_key(WORK_ITEM_NODE_TYPE_KEY)
        if node_type is None:
            raise WorkItemNodeTypeMissingError(
                f"workspace {workspace.id} has no node type carrying the 'work_item' role"
            )
        return node_type

    def get(self, work_item_id: WorkItemId) -> WorkItem:
        work_item = self._work_items.get(work_item_id)
        if work_item is None:
            raise WorkItemNotFoundError(f"work item {work_item_id} does not exist")
        return work_item

    def get_by_node(self, node_id: NodeId) -> WorkItem:
        work_item = self._work_items.get_by_node(node_id)
        if work_item is None:
            raise WorkItemNotFoundError(f"no work item backs node {node_id}")
        return work_item

    def list_workspace(
        self, workspace_id: WorkspaceId, *, include_archived: bool = False
    ) -> tuple[WorkItem, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._work_items.list_by_workspace(workspace_id)
                    if include_archived or not item.is_archived
                ),
                key=lambda item: item.created_at,
            )
        )

    def archive(self, workspace_id: WorkspaceId, work_item_id: WorkItemId) -> WorkItem:
        """Soft-archive a work item (default, recoverable lifecycle). The backing Node stays
        visible on the graph; only the work item is hidden from the Tasks list."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.archive_within(unit_of_work, workspace_id, work_item_id)

    def archive_within(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, work_item_id: WorkItemId
    ) -> WorkItem:
        return self._set_archived_within(unit_of_work, workspace_id, work_item_id, is_archived=True)

    def restore(self, workspace_id: WorkspaceId, work_item_id: WorkItemId) -> WorkItem:
        """Clear a work item's archive flag."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.restore_within(unit_of_work, workspace_id, work_item_id)

    def restore_within(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, work_item_id: WorkItemId
    ) -> WorkItem:
        return self._set_archived_within(
            unit_of_work, workspace_id, work_item_id, is_archived=False
        )

    def _set_archived_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        *,
        is_archived: bool,
    ) -> WorkItem:
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        if work_item.is_archived == is_archived:
            return work_item
        updated = work_item.model_copy(
            update={"is_archived": is_archived, "updated_at": datetime.now(UTC)}
        )
        unit_of_work.work_items.save_without_commit(updated)
        return updated

    def delete(self, workspace_id: WorkspaceId, work_item_id: WorkItemId) -> None:
        """Hard-delete a work item and its backing node together. Irrecoverable: checklist
        items cascade with the work item row, and deleting the node cascades any edges,
        placements, attachments, and file references that point at it. Callers must gate this
        behind an explicit destructive confirmation (ST-09)."""
        work_item = self.get(work_item_id)
        with self._unit_of_work_factory() as unit_of_work:
            self.delete_within(unit_of_work, workspace_id, work_item_id)
        if self._search_index is not None:
            self._search_index.remove_document(
                entity_type=SearchEntityType.NODE, entity_id=work_item.node_id
            )

    def delete_within(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, work_item_id: WorkItemId
    ) -> None:
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        unit_of_work.work_items.delete_without_commit(work_item.id)
        unit_of_work.nodes.delete_without_commit(work_item.node_id)

    def reparent(
        self,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        parent_id: WorkItemId | None,
    ) -> WorkItem:
        """Move a work item under a new legal parent (or to the hierarchy root with `None`),
        validating the child kind against the new parent exactly like create does."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.reparent_within(unit_of_work, workspace_id, work_item_id, parent_id)

    def reparent_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        parent_id: WorkItemId | None,
    ) -> WorkItem:
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        if parent_id is not None:
            self._require_legal_parent(unit_of_work, workspace_id, work_item.kind, parent_id)
        updated = work_item.model_copy(
            update={"parent_id": parent_id, "updated_at": datetime.now(UTC)}
        )
        unit_of_work.work_items.save_without_commit(updated)
        return updated

    def create(
        self,
        workspace_id: WorkspaceId,
        *,
        kind: WorkItemKind,
        work_type: WorkItemType,
        title: str,
        body: str = "",
        source: str,
        status: WorkItemStatus = WorkItemStatus.BACKLOG,
        parent_id: WorkItemId | None = None,
        repository_node_id: NodeId | None = None,
    ) -> tuple[WorkItem, Node]:
        """Create one `WorkItem` and its backing `Node` atomically, opening and committing its
        own unit of work. `create_within` remains the single lower-level mutation boundary for
        callers (e.g. `WorkPlanningService`) that must build a whole hierarchy in one
        transaction."""
        workspace = self.require_workspace(workspace_id)
        node_type = self.require_node_type(workspace)
        with self._unit_of_work_factory() as unit_of_work:
            work_item, node = self.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=kind,
                work_type=work_type,
                title=title,
                body=body,
                source=source,
                status=status,
                parent_id=parent_id,
                repository_node_id=repository_node_id,
            )
        # Search-index parity (S9-F03): the REST/UI create path must index the work item's
        # node text exactly like the MCP path does, so Tasks-created items are searchable
        # (scoped to the Tasks tab, ST-12).
        if self._search_index is not None:
            self._search_index.index_document(
                workspace_id=node.workspace_id,
                entity_type=SearchEntityType.NODE,
                entity_id=node.id,
                text=build_node_search_text(node, node_type),
                scope=SearchScope.TASKS,
            )
        return work_item, node

    def create_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace: Workspace,
        node_type: NodeType,
        *,
        kind: WorkItemKind,
        work_type: WorkItemType,
        title: str,
        body: str = "",
        source: str,
        status: WorkItemStatus = WorkItemStatus.BACKLOG,
        parent_id: WorkItemId | None = None,
        repository_node_id: NodeId | None = None,
    ) -> tuple[WorkItem, Node]:
        """Create one `WorkItem` and its backing `Node` atomically, into a caller-managed,
        already-open `unit_of_work`. Never opens its own transaction (mirrors
        `ResourceService.create_or_reuse_within`), so a caller building a whole Epic/Story/Task
        hierarchy in one pass commits it all together or not at all.
        """
        if parent_id is not None:
            self._require_legal_parent(unit_of_work, workspace.id, kind, parent_id)
        if repository_node_id is not None:
            self.require_existing_node(unit_of_work, workspace.id, repository_node_id)

        node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title=title, body=body)
        node.validate_against(node_type)
        work_item = WorkItem(
            workspace_id=workspace.id,
            node_id=node.id,
            kind=kind,
            work_type=work_type,
            status=status,
            parent_id=parent_id,
            repository_node_id=repository_node_id,
            source=source,
        )
        unit_of_work.nodes.save_without_commit(node)
        unit_of_work.work_items.save_without_commit(work_item)
        return work_item, node

    def update(
        self,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        patch: WorkItemUpdatePatch,
    ) -> WorkItem:
        """Apply one typed `patch` to a work item, opening and committing its own unit of work."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.update_within(unit_of_work, workspace_id, work_item_id, patch)

    def update_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        patch: WorkItemUpdatePatch,
    ) -> WorkItem:
        """Apply one typed `patch` to a work item inside a caller-managed unit of work.

        Validates the target belongs to `workspace_id` and that a newly assigned
        `repository_node_id` references an existing node there; enum/range/blank validation is
        delegated to the `WorkItem` model, which re-runs its validators on reconstruction.
        Title/body are the backing `Node`'s and stay out of this patch (they use the node
        update path), mirroring how `ResourceService` keeps its aggregate's title/body on the
        node."""
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)

        repository_node_id = work_item.repository_node_id
        if patch.clear_repository_node_id:
            repository_node_id = None
        elif patch.repository_node_id is not None:
            self.require_existing_node(unit_of_work, workspace_id, patch.repository_node_id)
            repository_node_id = patch.repository_node_id

        updated = WorkItem(
            id=work_item.id,
            workspace_id=work_item.workspace_id,
            node_id=work_item.node_id,
            kind=work_item.kind,
            work_type=patch.work_type if patch.work_type is not None else work_item.work_type,
            status=patch.status if patch.status is not None else work_item.status,
            parent_id=work_item.parent_id,
            repository_node_id=repository_node_id,
            priority=(
                None
                if patch.clear_priority
                else (patch.priority if patch.priority is not None else work_item.priority)
            ),
            due_date=(
                None
                if patch.clear_due_date
                else (patch.due_date if patch.due_date is not None else work_item.due_date)
            ),
            assignee=(
                None
                if patch.clear_assignee
                else (patch.assignee if patch.assignee is not None else work_item.assignee)
            ),
            blockers=(
                None
                if patch.clear_blockers
                else (patch.blockers if patch.blockers is not None else work_item.blockers)
            ),
            progress_percent=(
                None
                if patch.clear_progress_percent
                else (
                    patch.progress_percent
                    if patch.progress_percent is not None
                    else work_item.progress_percent
                )
            ),
            source=work_item.source,
            is_archived=work_item.is_archived,
            created_at=work_item.created_at,
            updated_at=datetime.now(UTC),
        )
        unit_of_work.work_items.save_without_commit(updated)
        return updated

    def _require_legal_parent(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        child_kind: WorkItemKind,
        parent_id: WorkItemId,
    ) -> None:
        required_parent_kind = _LEGAL_PARENT_KIND_BY_CHILD_KIND.get(child_kind)
        if required_parent_kind is None:
            raise InvalidWorkItemParentError(
                f"a work item of kind {child_kind} must not declare a parent_id"
            )
        parent = unit_of_work.work_items.get(parent_id)
        if parent is None or parent.workspace_id != workspace_id:
            raise InvalidWorkItemParentError(
                f"parent work item {parent_id} does not exist in workspace {workspace_id}"
            )
        if parent.kind is not required_parent_kind:
            raise InvalidWorkItemParentError(
                f"a work item of kind {child_kind} must have a parent of kind "
                f"{required_parent_kind}, but {parent_id} is {parent.kind}"
            )

    def require_existing_node(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, node_id: NodeId
    ) -> None:
        """Raise `RepositoryNodeNotFoundError` unless `node_id` references an existing Node in
        `workspace_id`. Public so a caller building a hierarchy around a caller-supplied
        repository reference (e.g. `WorkPlanningService`, review finding S5-R02) can validate it
        once, before any provider call, not only as a side effect of `create_within`."""
        node = unit_of_work.nodes.get(node_id)
        if node is None or node.workspace_id != workspace_id:
            raise RepositoryNodeNotFoundError(
                f"repository_node_id {node_id} does not reference an existing node in "
                f"workspace {workspace_id}"
            )

    def attach_document(
        self,
        *,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        document_id: DocumentId,
    ) -> DocumentLink:
        """Attach a Wiki document to a work item, opening and committing its own unit of work."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.attach_document_within(
                unit_of_work,
                workspace_id=workspace_id,
                work_item_id=work_item_id,
                document_id=document_id,
            )

    def attach_document_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        document_id: DocumentId,
    ) -> DocumentLink:
        """Create the many-to-many Wiki-link the accepted ST-05 contract requires (review
        finding S5-R03): a `DocumentLink(NODE)` between `document_id` and `work_item_id`'s
        backing node. Idempotent -- re-attaching an already-linked document returns the existing
        link rather than creating a duplicate. Both objects must exist in `workspace_id`."""
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        self._require_document_in_workspace(unit_of_work, workspace_id, document_id)

        existing = self._find_link(unit_of_work, document_id, work_item.node_id)
        if existing is not None:
            return existing
        link = DocumentLink(
            document_id=document_id,
            target_type=DocumentLinkTargetType.NODE,
            target_id=work_item.node_id,
        )
        unit_of_work.document_links.save_without_commit(link)
        return link

    def detach_document(
        self,
        *,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        document_id: DocumentId,
    ) -> None:
        """Detach a Wiki document from a work item, opening and committing its own unit of work."""
        with self._unit_of_work_factory() as unit_of_work:
            self.detach_document_within(
                unit_of_work,
                workspace_id=workspace_id,
                work_item_id=work_item_id,
                document_id=document_id,
            )

    def detach_document_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        document_id: DocumentId,
    ) -> None:
        """Remove the `DocumentLink` between `document_id` and `work_item_id`'s backing node, if
        one exists. A no-op when they were never linked -- detaching is idempotent."""
        work_item = self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        self._require_document_in_workspace(unit_of_work, workspace_id, document_id)

        existing = self._find_link(unit_of_work, document_id, work_item.node_id)
        if existing is not None:
            unit_of_work.document_links.delete_without_commit(existing.id)

    def list_linked_documents(self, work_item_id: WorkItemId) -> tuple[DocumentLink, ...]:
        """All `DocumentLink`s from the work item's backing node to documents. Read-only; the
        caller resolves each `document_id` to a title through the Wiki domain."""
        work_item = self.get(work_item_id)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.document_links.list_by_target(
                DocumentLinkTargetType.NODE, work_item.node_id
            )

    def list_checklist_items(self, work_item_id: WorkItemId) -> tuple[WorkItemChecklistItem, ...]:
        """A work item's checklist items in dense position order. Read-only."""
        self.get(work_item_id)
        return self._work_item_checklist_items.list_by_work_item(work_item_id)

    def get_checklist_item(
        self, checklist_item_id: WorkItemChecklistItemId
    ) -> WorkItemChecklistItem:
        """One checklist item by id, for the REST layer to resolve its owning workspace before
        a workspace-validated mutation. Read-only."""
        item = self._work_item_checklist_items.get(checklist_item_id)
        if item is None:
            raise WorkItemChecklistItemNotFoundError(
                f"checklist item {checklist_item_id} does not exist"
            )
        return item

    def add_checklist_item(
        self,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        label: str,
    ) -> WorkItemChecklistItem:
        """Append one checklist item, opening and committing its own unit of work."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.add_checklist_item_within(unit_of_work, workspace_id, work_item_id, label)

    def add_checklist_item_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        label: str,
    ) -> WorkItemChecklistItem:
        self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        items = unit_of_work.work_item_checklist_items.list_by_work_item(work_item_id)
        position = max((item.position for item in items), default=-1) + 1
        item = WorkItemChecklistItem(work_item_id=work_item_id, position=position, label=label)
        unit_of_work.work_item_checklist_items.save_without_commit(item)
        return item

    def update_checklist_item(
        self,
        workspace_id: WorkspaceId,
        checklist_item_id: WorkItemChecklistItemId,
        *,
        label: str | None = None,
        is_completed: bool | None = None,
    ) -> WorkItemChecklistItem:
        """Edit a checklist item's label and/or completion flag, opening and committing its own
        unit of work. Setting `is_completed` to its current value is a harmless idempotent
        toggle."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.update_checklist_item_within(
                unit_of_work,
                workspace_id,
                checklist_item_id,
                label=label,
                is_completed=is_completed,
            )

    def update_checklist_item_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        checklist_item_id: WorkItemChecklistItemId,
        *,
        label: str | None = None,
        is_completed: bool | None = None,
    ) -> WorkItemChecklistItem:
        item = self._require_checklist_item_in_workspace(
            unit_of_work, workspace_id, checklist_item_id
        )
        updated = item.model_copy(
            update={
                "label": label if label is not None else item.label,
                "is_completed": (is_completed if is_completed is not None else item.is_completed),
            }
        )
        unit_of_work.work_item_checklist_items.save_without_commit(updated)
        return updated

    def remove_checklist_item(
        self, workspace_id: WorkspaceId, checklist_item_id: WorkItemChecklistItemId
    ) -> None:
        """Delete a checklist item and renumber the remaining items to stay dense, opening and
        committing its own unit of work."""
        with self._unit_of_work_factory() as unit_of_work:
            self.remove_checklist_item_within(unit_of_work, workspace_id, checklist_item_id)

    def remove_checklist_item_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        checklist_item_id: WorkItemChecklistItemId,
    ) -> None:
        item = self._require_checklist_item_in_workspace(
            unit_of_work, workspace_id, checklist_item_id
        )
        unit_of_work.work_item_checklist_items.delete_without_commit(item.id)
        remaining = unit_of_work.work_item_checklist_items.list_by_work_item(item.work_item_id)
        for index, remaining_item in enumerate(remaining):
            if remaining_item.position != index:
                unit_of_work.work_item_checklist_items.save_without_commit(
                    remaining_item.model_copy(update={"position": index})
                )

    def reorder_checklist_items(
        self,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        ordered_ids: tuple[WorkItemChecklistItemId, ...],
    ) -> tuple[WorkItemChecklistItem, ...]:
        """Reposition a work item's checklist items to the given dense order, opening and
        committing its own unit of work. `ordered_ids` must reference exactly the current item
        ids, once each; anything else is rejected before any row changes."""
        with self._unit_of_work_factory() as unit_of_work:
            return self.reorder_checklist_items_within(
                unit_of_work, workspace_id, work_item_id, ordered_ids
            )

    def reorder_checklist_items_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        work_item_id: WorkItemId,
        ordered_ids: tuple[WorkItemChecklistItemId, ...],
    ) -> tuple[WorkItemChecklistItem, ...]:
        self._require_work_item_in_workspace(unit_of_work, workspace_id, work_item_id)
        items = unit_of_work.work_item_checklist_items.list_by_work_item(work_item_id)
        current_ids = {item.id for item in items}
        if set(ordered_ids) != current_ids or len(ordered_ids) != len(current_ids):
            raise InvalidChecklistOrderError(
                f"reorder for work item {work_item_id} must reference exactly its "
                f"{len(current_ids)} checklist item ids, once each"
            )
        by_id = {item.id: item for item in items}
        for position, item_id in enumerate(ordered_ids):
            item = by_id[item_id]
            if item.position != position:
                unit_of_work.work_item_checklist_items.save_without_commit(
                    item.model_copy(update={"position": position})
                )
        return tuple(unit_of_work.work_item_checklist_items.list_by_work_item(work_item_id))

    def _require_checklist_item_in_workspace(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        checklist_item_id: WorkItemChecklistItemId,
    ) -> WorkItemChecklistItem:
        item = unit_of_work.work_item_checklist_items.get(checklist_item_id)
        if item is None:
            raise WorkItemChecklistItemNotFoundError(
                f"checklist item {checklist_item_id} does not exist"
            )
        work_item = unit_of_work.work_items.get(item.work_item_id)
        if work_item is None or work_item.workspace_id != workspace_id:
            raise WorkItemNotFoundError(
                f"checklist item {checklist_item_id}'s work item is not in workspace {workspace_id}"
            )
        return item

    def _require_work_item_in_workspace(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, work_item_id: WorkItemId
    ) -> WorkItem:
        work_item = unit_of_work.work_items.get(work_item_id)
        if work_item is None or work_item.workspace_id != workspace_id:
            raise WorkItemNotFoundError(
                f"work item {work_item_id} does not exist in workspace {workspace_id}"
            )
        return work_item

    def _require_document_in_workspace(
        self, unit_of_work: ResearchUnitOfWork, workspace_id: WorkspaceId, document_id: DocumentId
    ) -> None:
        document = unit_of_work.documents.get(document_id)
        if document is None or document.workspace_id != workspace_id:
            raise WorkItemDocumentNotFoundError(
                f"document {document_id} does not exist in workspace {workspace_id}"
            )

    def _find_link(
        self, unit_of_work: ResearchUnitOfWork, document_id: DocumentId, node_id: NodeId
    ) -> DocumentLink | None:
        return next(
            (
                link
                for link in unit_of_work.document_links.list_by_document(document_id)
                if link.target_type is DocumentLinkTargetType.NODE and link.target_id == node_id
            ),
            None,
        )
