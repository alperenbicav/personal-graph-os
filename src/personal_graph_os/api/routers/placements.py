"""Placement routes: moving, resizing, or collapsing an existing placement."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_canvas_service
from personal_graph_os.api.schemas import UpdatePlacementRequest
from personal_graph_os.application.services import CanvasService
from personal_graph_os.domain.canvas import CanvasPlacement
from personal_graph_os.domain.identifiers import CanvasPlacementId

router = APIRouter(prefix="/placements", tags=["placements"])


@router.patch("/{placement_id}", response_model=CanvasPlacement)
def update_placement(
    placement_id: str,
    payload: UpdatePlacementRequest,
    canvas_service: CanvasService = Depends(get_canvas_service),
) -> CanvasPlacement:
    return canvas_service.update_placement(
        CanvasPlacementId(placement_id),
        position_x=payload.position_x,
        position_y=payload.position_y,
        width=payload.width,
        height=payload.height,
        is_collapsed=payload.is_collapsed,
    )
