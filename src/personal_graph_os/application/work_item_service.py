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

from personal_graph_os.application.repositories import WorkItemRepository, WorkspaceRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.semantic_keys import WORK_ITEM_NODE_TYPE_KEY
from personal_graph_os.application.services import WorkspaceNotFoundError
from personal_graph_os.domain.documents import DocumentLink, DocumentLinkTargetType
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import DocumentId, NodeId, WorkItemId, WorkspaceId
from personal_graph_os.domain.schema import NodeType, Workspace
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemKind,
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


# An Epic's kind has no entry here: `WorkItem.model_post_init` already rejects any `parent_id`
# for it, so `_require_legal_parent` is never even reached for that case in practice.
_LEGAL_PARENT_KIND_BY_CHILD_KIND: dict[WorkItemKind, WorkItemKind] = {
    WorkItemKind.STORY: WorkItemKind.EPIC,
    WorkItemKind.TASK: WorkItemKind.STORY,
}


class WorkItemService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        work_items: WorkItemRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._work_items = work_items
        self._unit_of_work_factory = unit_of_work_factory

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
