"""Resource routes: create-or-reuse, list/get, lifecycle updates, and archive all go through
`ResourceService`. Every response combines the `Resource` with its backing `Node`'s title/body
(product decision #2: a `Resource` never carries its own copy of them).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from personal_graph_os.api.dependencies import get_node_repository, get_resource_service
from personal_graph_os.api.schemas import (
    CreateOrReuseResourceRequest,
    ResourceResponse,
    UpdateResourceRequest,
)
from personal_graph_os.application.repositories import NodeRepository
from personal_graph_os.application.services import NodeNotFoundError, ResourceService
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId
from personal_graph_os.domain.resource import Resource

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
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> list[ResourceResponse]:
    resources = resource_service.list_by_workspace(WorkspaceId(workspace_id))
    return [_combine(resource, nodes) for resource in resources]


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(
    resource_id: str,
    resource_service: ResourceService = Depends(get_resource_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResourceResponse:
    resource = resource_service.get(ResourceId(resource_id))
    return _combine(resource, nodes)


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
