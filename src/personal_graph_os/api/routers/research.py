"""Research dashboard and settings routes: the six built-in resurfacing views
(`ResearchDashboardService`) and per-workspace resurfacing settings (`ResearchSettingsService`).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import (
    get_node_repository,
    get_research_dashboard_service,
    get_research_settings_service,
)
from personal_graph_os.api.schemas import (
    ResearchDashboardResponse,
    ResourceResponse,
    UpdateResearchSettingsRequest,
)
from personal_graph_os.application.repositories import NodeRepository
from personal_graph_os.application.research_dashboard import ResearchDashboardService
from personal_graph_os.application.services import NodeNotFoundError, ResearchSettingsService
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import Resource

router = APIRouter(tags=["research"])


def _combine_all(resources: tuple[Resource, ...], nodes: NodeRepository) -> list[ResourceResponse]:
    responses = []
    for resource in resources:
        node = nodes.get(resource.node_id)
        if node is None:
            raise NodeNotFoundError(
                f"resource {resource.id}'s backing node {resource.node_id} is missing"
            )
        responses.append(ResourceResponse.from_resource_and_node(resource, node))
    return responses


@router.get("/research/dashboard", response_model=ResearchDashboardResponse)
def get_research_dashboard(
    workspace_id: str,
    dashboard_service: ResearchDashboardService = Depends(get_research_dashboard_service),
    nodes: NodeRepository = Depends(get_node_repository),
) -> ResearchDashboardResponse:
    typed_workspace_id = WorkspaceId(workspace_id)
    as_of = datetime.now(UTC)
    return ResearchDashboardResponse(
        inbox=_combine_all(dashboard_service.inbox(typed_workspace_id), nodes),
        continue_reading=_combine_all(
            dashboard_service.continue_reading(typed_workspace_id), nodes
        ),
        stale=_combine_all(dashboard_service.stale(typed_workspace_id, as_of=as_of), nodes),
        needs_takeaway=_combine_all(dashboard_service.needs_takeaway(typed_workspace_id), nodes),
        unlinked=_combine_all(dashboard_service.unlinked(typed_workspace_id), nodes),
        applied=_combine_all(dashboard_service.applied(typed_workspace_id), nodes),
    )


@router.get("/research-settings", response_model=WorkspaceResearchSettings)
def get_research_settings(
    workspace_id: str,
    settings_service: ResearchSettingsService = Depends(get_research_settings_service),
) -> WorkspaceResearchSettings:
    return settings_service.get_or_default(WorkspaceId(workspace_id))


@router.patch("/research-settings", response_model=WorkspaceResearchSettings)
def update_research_settings(
    workspace_id: str,
    payload: UpdateResearchSettingsRequest,
    settings_service: ResearchSettingsService = Depends(get_research_settings_service),
) -> WorkspaceResearchSettings:
    return settings_service.update(
        WorkspaceId(workspace_id), stale_after_days=payload.stale_after_days
    )
