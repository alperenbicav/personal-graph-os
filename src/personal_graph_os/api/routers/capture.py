"""Channel-neutral capture + `plan_work` composition route (EP-2026-012 ST-05, review finding
S5-R01): the smallest bounded invocation that actually reaches `CaptureService` and, when a
`plan` operation is pending and a provider is configured, `WorkPlanningService` too -- through
the same `CapturePlanningOrchestrator` every offline test exercises.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_capture_planning_orchestrator
from personal_graph_os.api.schemas import (
    CaptureAndPlanResponse,
    CaptureOutcomeResponse,
    CaptureRequest,
    WorkPlanOutcomeResponse,
)
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.domain.capture import CaptureEnvelope
from personal_graph_os.domain.identifiers import FileReferenceId, NodeId, WorkspaceId

router = APIRouter(prefix="/capture", tags=["capture"])


@router.post("", response_model=CaptureAndPlanResponse)
def submit_capture(
    payload: CaptureRequest,
    orchestrator: CapturePlanningOrchestrator = Depends(get_capture_planning_orchestrator),
) -> CaptureAndPlanResponse:
    envelope = CaptureEnvelope(
        workspace_id=WorkspaceId(payload.workspace_id),
        source=payload.source,
        request_id=payload.request_id,
        actor_name=payload.actor_name,
        payload_kind=payload.payload_kind,
        url=payload.url,
        file_reference_id=(
            FileReferenceId(payload.file_reference_id) if payload.file_reference_id else None
        ),
        external_item_id=payload.external_item_id,
        text=payload.text,
        intent=payload.intent,
        title=payload.title,
    )
    repository_node_id = NodeId(payload.repository_node_id) if payload.repository_node_id else None

    outcome, plan_outcome = orchestrator.submit(envelope, repository_node_id=repository_node_id)
    return CaptureAndPlanResponse(
        capture=CaptureOutcomeResponse.from_outcome(outcome),
        plan=(
            WorkPlanOutcomeResponse.from_outcome(plan_outcome) if plan_outcome is not None else None
        ),
    )
