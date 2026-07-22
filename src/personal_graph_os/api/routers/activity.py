"""Activity routes: bounded, cursor-paginated reads over the append-only audit trail.

Read-only in ST-07.1; undo lands in ST-07.3 as a dedicated `POST .../undo` route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_activity_service
from personal_graph_os.api.schemas import ActivityEventPageResponse
from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.domain.activity import ActivityEvent
from personal_graph_os.domain.identifiers import ActivityEventId, WorkspaceId

router = APIRouter(prefix="/activity-events", tags=["activity"])


@router.get("", response_model=ActivityEventPageResponse)
def list_activity_events(
    workspace_id: str,
    limit: int = 50,
    cursor: str | None = None,
    activity_service: ActivityService = Depends(get_activity_service),
) -> ActivityEventPageResponse:
    page = activity_service.list_workspace_events(
        WorkspaceId(workspace_id), limit=limit, cursor=cursor
    )
    return ActivityEventPageResponse.from_page(page)


@router.get("/{event_id}", response_model=ActivityEvent)
def get_activity_event(
    workspace_id: str,
    event_id: str,
    activity_service: ActivityService = Depends(get_activity_service),
) -> ActivityEvent:
    return activity_service.get_event(WorkspaceId(workspace_id), ActivityEventId(event_id))
