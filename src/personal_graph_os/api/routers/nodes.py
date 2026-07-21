"""Node routes: capture, update, and archive all go through `NodeService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_node_repository, get_node_service
from personal_graph_os.api.schemas import CaptureNodeRequest, UpdateNodeRequest
from personal_graph_os.application.repositories import NodeRepository
from personal_graph_os.application.services import NodeService
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import NodeId, NodeTypeId, StatusDefinitionId, WorkspaceId

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=list[Node])
def list_nodes(
    workspace_id: str,
    include_archived: bool = False,
    nodes: NodeRepository = Depends(get_node_repository),
) -> list[Node]:
    return list(
        nodes.list_by_workspace(WorkspaceId(workspace_id), include_archived=include_archived)
    )


@router.post("", response_model=Node, status_code=201)
def capture_node(
    payload: CaptureNodeRequest, node_service: NodeService = Depends(get_node_service)
) -> Node:
    return node_service.capture(
        WorkspaceId(payload.workspace_id), NodeTypeId(payload.node_type_id), payload.title
    )


@router.patch("/{node_id}", response_model=Node)
def update_node(
    node_id: str,
    payload: UpdateNodeRequest,
    node_service: NodeService = Depends(get_node_service),
) -> Node:
    return node_service.update(
        NodeId(node_id),
        title=payload.title,
        body=payload.body,
        status_id=StatusDefinitionId(payload.status_id) if payload.status_id else None,
        field_values=payload.field_values,
    )


@router.delete("/{node_id}", response_model=Node)
def archive_node(node_id: str, node_service: NodeService = Depends(get_node_service)) -> Node:
    return node_service.archive(NodeId(node_id))
