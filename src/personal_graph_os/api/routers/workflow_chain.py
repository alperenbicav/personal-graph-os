"""Guided workflow chain route: advance one Resource -> Takeaway -> Decision -> Task ->
Implementation step, via `WorkflowChainService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_workflow_chain_service
from personal_graph_os.api.schemas import AdvanceWorkflowChainRequest, WorkflowChainStepResponse
from personal_graph_os.application.workflow_chain import WorkflowChainService
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId

router = APIRouter(prefix="/workflow-chain", tags=["workflow-chain"])


@router.post("/advance", response_model=WorkflowChainStepResponse, status_code=201)
def advance_workflow_chain(
    payload: AdvanceWorkflowChainRequest,
    workflow_chain_service: WorkflowChainService = Depends(get_workflow_chain_service),
) -> WorkflowChainStepResponse:
    node, edge = workflow_chain_service.advance(
        WorkspaceId(payload.workspace_id),
        NodeId(payload.source_node_id),
        payload.step,
        title=payload.title,
        existing_target_node_id=(
            NodeId(payload.existing_target_node_id) if payload.existing_target_node_id else None
        ),
    )
    return WorkflowChainStepResponse(node=node, edge=edge)
