"""Work-item routes (EP-2026-012 ST-08): the Tasks workspace's Epic/Story/Task lifecycle,
planning fields, checklists, and Wiki links, all through `WorkItemService` using the same
application services as the MCP tools. Every response combines the `WorkItem` with its backing
`Node`'s title/body, mirroring the Resources routes; title/body edits reuse the node update path."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_node_repository, get_work_item_service
from personal_graph_os.api.schemas import (
    AttachWorkItemWikiLinkRequest,
    CreateChecklistItemRequest,
    CreateWorkItemRequest,
    ReorderChecklistItemsRequest,
    UpdateChecklistItemRequest,
    UpdateWorkItemRequest,
    WorkItemDetailResponse,
    WorkItemResponse,
)
from personal_graph_os.application.repositories import NodeRepository
from personal_graph_os.application.services import NodeNotFoundError
from personal_graph_os.application.work_item_service import (
    WorkItemService,
    WorkItemUpdatePatch,
)
from personal_graph_os.domain.documents import DocumentLink
from personal_graph_os.domain.identifiers import (
    DocumentId,
    NodeId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.work_items import WorkItem, WorkItemChecklistItem

router = APIRouter(prefix="/work-items", tags=["work-items"])
# Checklist items are addressed directly by their stable id (a PATCH/DELETE does not need the
# owning work item), so they live on their own top-level router rather than being nested under
# `/work-items/checklist-items/...`.
checklist_router = APIRouter(prefix="/checklist-items", tags=["work-items"])


def _combine(work_item: WorkItem, nodes: NodeRepository) -> WorkItemResponse:
    node = nodes.get(work_item.node_id)
    if node is None:
        raise NodeNotFoundError(
            f"work item {work_item.id}'s backing node {work_item.node_id} is missing"
        )
    return WorkItemResponse.from_work_item_and_node(work_item, node)


def _workspace_of_work_item(
    work_item_service: WorkItemService, work_item_id: WorkItemId
) -> WorkspaceId:
    """The work item itself is the source of its workspace; the REST caller never needs to
    know it ahead of time, and the service re-validates membership on every mutation."""
    return work_item_service.get(work_item_id).workspace_id


def _workspace_of_checklist_item(
    work_item_service: WorkItemService, checklist_item_id: WorkItemChecklistItemId
) -> WorkspaceId:
    item = work_item_service.get_checklist_item(checklist_item_id)
    return work_item_service.get(item.work_item_id).workspace_id


@router.get("", response_model=list[WorkItemResponse])
def list_work_items(
    workspace_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> list[WorkItemResponse]:
    return [
        _combine(work_item, nodes)
        for work_item in work_item_service.list_workspace(WorkspaceId(workspace_id))
    ]


@router.get("/{work_item_id}", response_model=WorkItemResponse)
def get_work_item(
    work_item_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> WorkItemResponse:
    return _combine(work_item_service.get(WorkItemId(work_item_id)), nodes)


@router.get("/{work_item_id}/detail", response_model=WorkItemDetailResponse)
def get_work_item_detail(
    work_item_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> WorkItemDetailResponse:
    work_item = work_item_service.get(WorkItemId(work_item_id))
    return WorkItemDetailResponse(
        work_item=_combine(work_item, nodes),
        checklist_items=list(work_item_service.list_checklist_items(work_item.id)),
        linked_documents=list(work_item_service.list_linked_documents(work_item.id)),
    )


@router.post("", response_model=WorkItemResponse, status_code=201)
def create_work_item(
    payload: CreateWorkItemRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> WorkItemResponse:
    work_item, _ = work_item_service.create(
        WorkspaceId(payload.workspace_id),
        kind=payload.kind,
        work_type=payload.work_type,
        title=payload.title,
        body=payload.body,
        source=payload.source,
        status=payload.status,
        parent_id=WorkItemId(payload.parent_id) if payload.parent_id is not None else None,
        repository_node_id=NodeId(payload.repository_node_id)
        if payload.repository_node_id is not None
        else None,
    )
    return _combine(work_item, nodes)


@router.patch("/{work_item_id}", response_model=WorkItemResponse)
def update_work_item(
    work_item_id: str,
    payload: UpdateWorkItemRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> WorkItemResponse:
    work_item = work_item_service.update(
        _workspace_of_work_item(work_item_service, WorkItemId(work_item_id)),
        WorkItemId(work_item_id),
        WorkItemUpdatePatch(
            work_type=payload.work_type,
            status=payload.status,
            priority=payload.priority,
            due_date=payload.due_date,
            assignee=payload.assignee,
            blockers=payload.blockers,
            progress_percent=payload.progress_percent,
            repository_node_id=(
                NodeId(payload.repository_node_id)
                if payload.repository_node_id is not None
                else None
            ),
            clear_priority=payload.clear_priority,
            clear_due_date=payload.clear_due_date,
            clear_assignee=payload.clear_assignee,
            clear_blockers=payload.clear_blockers,
            clear_progress_percent=payload.clear_progress_percent,
            clear_repository_node_id=payload.clear_repository_node_id,
        ),
    )
    return _combine(work_item, nodes)


@router.get("/{work_item_id}/checklist-items", response_model=list[WorkItemChecklistItem])
def list_checklist_items(
    work_item_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> list[WorkItemChecklistItem]:
    return list(work_item_service.list_checklist_items(WorkItemId(work_item_id)))


@router.post(
    "/{work_item_id}/checklist-items", response_model=WorkItemChecklistItem, status_code=201
)
def add_checklist_item(
    work_item_id: str,
    payload: CreateChecklistItemRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> WorkItemChecklistItem:
    return work_item_service.add_checklist_item(
        _workspace_of_work_item(work_item_service, WorkItemId(work_item_id)),
        WorkItemId(work_item_id),
        label=payload.label,
    )


@router.post("/{work_item_id}/checklist-items/reorder", response_model=list[WorkItemChecklistItem])
def reorder_checklist_items(
    work_item_id: str,
    payload: ReorderChecklistItemsRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> list[WorkItemChecklistItem]:
    return list(
        work_item_service.reorder_checklist_items(
            _workspace_of_work_item(work_item_service, WorkItemId(work_item_id)),
            WorkItemId(work_item_id),
            tuple(WorkItemChecklistItemId(item_id) for item_id in payload.ordered_ids),
        )
    )


@checklist_router.patch("/{checklist_item_id}", response_model=WorkItemChecklistItem)
def update_checklist_item(
    checklist_item_id: str,
    payload: UpdateChecklistItemRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> WorkItemChecklistItem:
    return work_item_service.update_checklist_item(
        _workspace_of_checklist_item(work_item_service, WorkItemChecklistItemId(checklist_item_id)),
        WorkItemChecklistItemId(checklist_item_id),
        label=payload.label,
        is_completed=payload.is_completed,
    )


@checklist_router.delete("/{checklist_item_id}", status_code=204)
def remove_checklist_item(
    checklist_item_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> None:
    work_item_service.remove_checklist_item(
        _workspace_of_checklist_item(work_item_service, WorkItemChecklistItemId(checklist_item_id)),
        WorkItemChecklistItemId(checklist_item_id),
    )


@router.post("/{work_item_id}/wiki-links", response_model=DocumentLink, status_code=201)
def attach_wiki_link(
    work_item_id: str,
    payload: AttachWorkItemWikiLinkRequest,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> DocumentLink:
    return work_item_service.attach_document(
        workspace_id=_workspace_of_work_item(work_item_service, WorkItemId(work_item_id)),
        work_item_id=WorkItemId(work_item_id),
        document_id=DocumentId(payload.document_id),
    )


@router.delete("/{work_item_id}/wiki-links", status_code=204)
def detach_wiki_link(
    work_item_id: str,
    document_id: str,
    work_item_service: WorkItemService = Depends(get_work_item_service),
) -> None:
    work_item_service.detach_document(
        workspace_id=_workspace_of_work_item(work_item_service, WorkItemId(work_item_id)),
        work_item_id=WorkItemId(work_item_id),
        document_id=DocumentId(document_id),
    )
