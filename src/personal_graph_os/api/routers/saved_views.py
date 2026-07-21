"""Saved view routes: named, reusable table/Kanban/timeline projections, via
`SavedViewService`. Filters/sort are always the typed `ProjectionQuery` shape (see
`domain/views.py`); nothing here accepts a raw filter/sort expression.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_saved_view_service
from personal_graph_os.api.schemas import CreateSavedViewRequest, UpdateSavedViewRequest
from personal_graph_os.application.services import SavedViewService
from personal_graph_os.domain.identifiers import SavedViewId, WorkspaceId
from personal_graph_os.domain.views import SavedView

router = APIRouter(prefix="/saved-views", tags=["saved-views"])


@router.get("", response_model=list[SavedView])
def list_saved_views(
    workspace_id: str,
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> list[SavedView]:
    return list(saved_view_service.list_by_workspace(WorkspaceId(workspace_id)))


@router.get("/{saved_view_id}", response_model=SavedView)
def get_saved_view(
    saved_view_id: str,
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> SavedView:
    return saved_view_service.get(SavedViewId(saved_view_id))


@router.post("", response_model=SavedView, status_code=201)
def create_saved_view(
    payload: CreateSavedViewRequest,
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> SavedView:
    return saved_view_service.create(
        WorkspaceId(payload.workspace_id), payload.name, payload.view_kind, payload.query
    )


@router.patch("/{saved_view_id}", response_model=SavedView)
def update_saved_view(
    saved_view_id: str,
    payload: UpdateSavedViewRequest,
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> SavedView:
    return saved_view_service.update(
        SavedViewId(saved_view_id), name=payload.name, query=payload.query
    )


@router.delete("/{saved_view_id}", status_code=204)
def delete_saved_view(
    saved_view_id: str,
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> None:
    saved_view_service.delete(SavedViewId(saved_view_id))
