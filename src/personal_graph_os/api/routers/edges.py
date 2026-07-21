"""Edge routes: connecting two nodes always goes through `EdgeService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_edge_repository, get_edge_service
from personal_graph_os.api.schemas import ConnectEdgeRequest
from personal_graph_os.application.repositories import EdgeRepository
from personal_graph_os.application.services import EdgeService
from personal_graph_os.domain.graph import Edge
from personal_graph_os.domain.identifiers import EdgeTypeId, NodeId, WorkspaceId

router = APIRouter(prefix="/edges", tags=["edges"])


@router.get("", response_model=list[Edge])
def list_edges(
    workspace_id: str, edges: EdgeRepository = Depends(get_edge_repository)
) -> list[Edge]:
    return list(edges.list_by_workspace(WorkspaceId(workspace_id)))


@router.post("", response_model=Edge, status_code=201)
def connect_edge(
    payload: ConnectEdgeRequest, edge_service: EdgeService = Depends(get_edge_service)
) -> Edge:
    return edge_service.connect(
        WorkspaceId(payload.workspace_id),
        EdgeTypeId(payload.edge_type_id),
        NodeId(payload.source_node_id),
        NodeId(payload.target_node_id),
    )
