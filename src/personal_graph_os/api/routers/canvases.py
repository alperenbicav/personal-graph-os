"""Canvas routes: creating a canvas and placing a node on it go through `CanvasService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import (
    get_canvas_repository,
    get_canvas_service,
    get_placement_repository,
)
from personal_graph_os.api.schemas import CreateCanvasRequest, PlaceNodeRequest
from personal_graph_os.application.repositories import CanvasPlacementRepository, CanvasRepository
from personal_graph_os.application.services import CanvasService
from personal_graph_os.domain.canvas import Canvas, CanvasPlacement
from personal_graph_os.domain.identifiers import CanvasId, NodeId, WorkspaceId

router = APIRouter(prefix="/canvases", tags=["canvases"])


@router.get("", response_model=list[Canvas])
def list_canvases(
    workspace_id: str, canvases: CanvasRepository = Depends(get_canvas_repository)
) -> list[Canvas]:
    return list(canvases.list_by_workspace(WorkspaceId(workspace_id)))


@router.post("", response_model=Canvas, status_code=201)
def create_canvas(
    payload: CreateCanvasRequest, canvas_service: CanvasService = Depends(get_canvas_service)
) -> Canvas:
    return canvas_service.create_canvas(WorkspaceId(payload.workspace_id), payload.name)


@router.get("/{canvas_id}/placements", response_model=list[CanvasPlacement])
def list_placements(
    canvas_id: str, placements: CanvasPlacementRepository = Depends(get_placement_repository)
) -> list[CanvasPlacement]:
    return list(placements.list_by_canvas(CanvasId(canvas_id)))


@router.post("/{canvas_id}/placements", response_model=CanvasPlacement, status_code=201)
def place_node(
    canvas_id: str,
    payload: PlaceNodeRequest,
    canvas_service: CanvasService = Depends(get_canvas_service),
) -> CanvasPlacement:
    return canvas_service.place_node(
        CanvasId(canvas_id),
        NodeId(payload.node_id),
        position_x=payload.position_x,
        position_y=payload.position_y,
    )
