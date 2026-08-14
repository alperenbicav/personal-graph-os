"""Resource routes: create-or-reuse, list/get, lifecycle updates, and archive all go through
`ResourceService`. Every response combines the `Resource` with its backing `Node`'s title/body
(product decision #2: a `Resource` never carries its own copy of them).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from personal_graph_os.api.dependencies import (
    get_node_repository,
    get_resource_detail_service,
    get_resource_service,
)
from personal_graph_os.api.schemas import (
    CreateOrReuseResourceRequest,
    ResourceDetailResponse,
    ResourceResponse,
    UpdateResourceRequest,
)
from personal_graph_os.application.repositories import NodeRepository
from personal_graph_os.application.resource_detail_service import ResourceDetailService
from personal_graph_os.application.services import NodeNotFoundError, ResourceService
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId
from personal_graph_os.domain.resource import (
    RepositoryLabel,
    Resource,
    ResourceKind,
    ResourceLifecycleStatus,
)

router = APIRouter(prefix="/resources", tags=["resources"])


def _combine(resource: Resource, nodes: NodeRepository) -> ResourceResponse:
    node = nodes.get(resource.node_id)
    if node is None:
        raise NodeNotFoundError(
            f"resource {resource.id}'s backing node {resource.node_id} is missing"
        )
    return ResourceResponse.from_resource_and_node(resource, node)


@router.get("", response_model=list[ResourceResponse])
def list_resources(
    workspace_id: str,
    kind: ResourceKind | None = None,
    lifecycle_status: ResourceLifecycleStatus | None = None,
    repository_label: RepositoryLabel | None = None,
    last_activity_since: datetime | None = None,
    last_activity_until: datetime | None = None,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> list[ResourceResponse]:
    resources = resource_service.list_by_workspace(
        WorkspaceId(workspace_id),
        kind=kind,
        lifecycle_status=lifecycle_status,
        repository_label=repository_label,
        last_activity_since=last_activity_since,
        last_activity_until=last_activity_until,
    )
    return [_combine(resource, nodes) for resource in resources]


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(
    resource_id: str,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceResponse:
    resource = resource_service.get(ResourceId(resource_id))
    return _combine(resource, nodes)


@router.get("/{resource_id}/detail", response_model=ResourceDetailResponse)
def get_resource_detail(
    resource_id: str,
    detail_service: ResourceDetailService = Depends(get_resource_detail_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceDetailResponse:
    """Everything the Research/Repositories master-detail panel needs in one round trip
    (EP-2026-012 ST-06): the resource's own fields plus its current enrichment, graph
    relations, related Wiki documents, and ingestion-job provenance."""
    detail = detail_service.get_detail(ResourceId(resource_id))
    node = nodes.get(detail.resource.node_id)
    if node is None:
        raise NodeNotFoundError(
            f"resource {detail.resource.id}'s backing node {detail.resource.node_id} is missing"
        )
    return ResourceDetailResponse.from_detail(detail, node)


@router.post("", response_model=ResourceResponse)
def create_or_reuse_resource(
    payload: CreateOrReuseResourceRequest,
    response: Response,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceResponse:
    resource, was_created = resource_service.create_or_reuse(
        WorkspaceId(payload.workspace_id),
        payload.title,
        payload.raw_source,
        kind=payload.kind,
        body=payload.body,
    )
    response.status_code = 201 if was_created else 200
    return _combine(resource, nodes)


@router.patch("/{resource_id}", response_model=ResourceResponse)
def update_resource(
    resource_id: str,
    payload: UpdateResourceRequest,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceResponse:
    resource = resource_service.update(
        ResourceId(resource_id),
        lifecycle_status=payload.lifecycle_status,
        next_action=payload.next_action,
        clear_next_action=payload.clear_next_action,
        next_action_dismissed=payload.next_action_dismissed,
        open_questions=(
            tuple(payload.open_questions) if payload.open_questions is not None else None
        ),
        takeaways=tuple(payload.takeaways) if payload.takeaways is not None else None,
        progress_percent=payload.progress_percent,
        clear_progress_percent=payload.clear_progress_percent,
        review_at=payload.review_at,
        clear_review_at=payload.clear_review_at,
        repository_label=payload.repository_label,
        clear_repository_label=payload.clear_repository_label,
    )
    return _combine(resource, nodes)


@router.delete("/{resource_id}", response_model=ResourceResponse)
def archive_resource(
    resource_id: str,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceResponse:
    resource = resource_service.archive(ResourceId(resource_id))
    return _combine(resource, nodes)


@router.delete("/{resource_id}/hard", response_model=None, status_code=204)
def hard_delete_resource(
    resource_id: str,
    confirm_id: str,
    resource_service: ResourceService = Depends(get_resource_service),
) -> None:
    """Irrecoverable removal of a research resource and its backing node (ST-09). The caller
    must confirm by echoing the resource id -- the same destructive gate the MCP tool applies --
    so a stray UI click can never destroy a resource."""
    if confirm_id != resource_id:
        raise HTTPException(
            status_code=422,
            detail="confirm_id must match the resource id being deleted",
        )
    resource_service.delete(ResourceId(resource_id))
