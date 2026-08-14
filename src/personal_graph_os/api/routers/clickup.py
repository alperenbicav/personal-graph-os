"""ClickUp channel ingress route (EP-2026-012 ST-10): the REST surface for importing a
user-selected ClickUp task through the same `CapturePlanningOrchestrator` every other channel
uses. Returns the same `CaptureAndPlanResponse` shape as `/capture` so raw and planned modes are
observably identical to agent input. Fails closed with a typed 503 when no `PGOS_CLICKUP_API_TOKEN`
is configured; an unknown task is a typed 404, and re-importing the same task id with changed
content is a typed 409 conflict (never a silent replay).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_clickup_service
from personal_graph_os.api.schemas import (
    CaptureAndPlanResponse,
    CaptureOutcomeResponse,
    ClickUpImportRequest,
    WorkPlanOutcomeResponse,
)
from personal_graph_os.application.clickup_service import ClickupService
from personal_graph_os.domain.capture import CaptureIntent
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId

router = APIRouter(prefix="/clickup", tags=["clickup"])


@router.post("/import", response_model=CaptureAndPlanResponse)
def import_clickup_item(
    payload: ClickUpImportRequest,
    clickup_service: ClickupService = Depends(get_clickup_service),
) -> CaptureAndPlanResponse:
    outcome, plan_outcome = clickup_service.import_item(
        WorkspaceId(payload.workspace_id),
        task_id=payload.task_id,
        intent=CaptureIntent(payload.intent),
        actor_name=payload.actor_name,
        repository_node_id=(
            NodeId(payload.repository_node_id) if payload.repository_node_id else None
        ),
    )
    return CaptureAndPlanResponse(
        capture=CaptureOutcomeResponse.from_outcome(outcome),
        plan=(
            WorkPlanOutcomeResponse.from_outcome(plan_outcome) if plan_outcome is not None else None
        ),
    )
