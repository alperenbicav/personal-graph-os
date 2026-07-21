"""Structured projection routes: table/Kanban/timeline over canonical Node/Resource data,
via `ProjectionService`. Each accepts either an ad-hoc `query`, or a `saved_view_id` to
replay a persisted projection exactly as saved (reload persistence)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_projection_service, get_saved_view_service
from personal_graph_os.api.schemas import (
    KanbanViewRequest,
    ProjectionItemResponse,
    TableViewRequest,
    TimelineViewRequest,
)
from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.services import SavedViewService
from personal_graph_os.domain.identifiers import SavedViewId, WorkspaceId
from personal_graph_os.domain.views import ProjectionQuery

router = APIRouter(prefix="/views", tags=["views"])


def _resolve_query(
    saved_view_service: SavedViewService, *, query: ProjectionQuery, saved_view_id: str | None
) -> ProjectionQuery:
    if saved_view_id is None:
        return query
    return saved_view_service.get(SavedViewId(saved_view_id)).to_projection_query()


@router.post("/table", response_model=list[ProjectionItemResponse])
def evaluate_table(
    payload: TableViewRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> list[ProjectionItemResponse]:
    query = _resolve_query(
        saved_view_service, query=payload.query, saved_view_id=payload.saved_view_id
    )
    rows = projection_service.evaluate_table(WorkspaceId(payload.workspace_id), query)
    return [ProjectionItemResponse.from_item(item) for item in rows]


@router.post("/kanban", response_model=dict[str, list[ProjectionItemResponse]])
def evaluate_kanban(
    payload: KanbanViewRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> dict[str, list[ProjectionItemResponse]]:
    query = _resolve_query(
        saved_view_service, query=payload.query, saved_view_id=payload.saved_view_id
    )
    columns = projection_service.evaluate_kanban(
        WorkspaceId(payload.workspace_id), query, payload.group_by
    )
    return {
        key: [ProjectionItemResponse.from_item(item) for item in items]
        for key, items in columns.items()
    }


@router.post("/timeline", response_model=list[ProjectionItemResponse])
def evaluate_timeline(
    payload: TimelineViewRequest,
    projection_service: ProjectionService = Depends(get_projection_service),
    saved_view_service: SavedViewService = Depends(get_saved_view_service),
) -> list[ProjectionItemResponse]:
    query = _resolve_query(
        saved_view_service, query=payload.query, saved_view_id=payload.saved_view_id
    )
    rows = projection_service.evaluate_timeline(
        WorkspaceId(payload.workspace_id), query, payload.date_field
    )
    return [ProjectionItemResponse.from_item(item) for item in rows]
