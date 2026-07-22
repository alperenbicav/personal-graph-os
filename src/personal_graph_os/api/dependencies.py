"""FastAPI dependency accessors into the composition root stored on `app.state`.

`create_app()` builds one connection/repository/service graph per process and stores it on
`app.state`; these functions just hand pieces of it to route handlers.
"""

from __future__ import annotations

from fastapi import Request

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.repositories import (
    CanvasPlacementRepository,
    CanvasRepository,
    EdgeRepository,
    NodeRepository,
    ResourceRepository,
    SavedViewRepository,
    SearchIndexRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_dashboard import ResearchDashboardService
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.services import (
    CanvasService,
    EdgeService,
    NodeService,
    ResearchSettingsService,
    ResourceService,
    SavedViewService,
    SchemaService,
)
from personal_graph_os.application.workflow_chain import WorkflowChainService
from personal_graph_os.domain.identifiers import CanvasId, WorkspaceId


def get_workspace_repository(request: Request) -> WorkspaceRepository:
    return request.app.state.workspace_repository


def get_node_repository(request: Request) -> NodeRepository:
    return request.app.state.node_repository


def get_edge_repository(request: Request) -> EdgeRepository:
    return request.app.state.edge_repository


def get_canvas_repository(request: Request) -> CanvasRepository:
    return request.app.state.canvas_repository


def get_placement_repository(request: Request) -> CanvasPlacementRepository:
    return request.app.state.placement_repository


def get_node_service(request: Request) -> NodeService:
    return request.app.state.node_service


def get_edge_service(request: Request) -> EdgeService:
    return request.app.state.edge_service


def get_canvas_service(request: Request) -> CanvasService:
    return request.app.state.canvas_service


def get_schema_service(request: Request) -> SchemaService:
    return request.app.state.schema_service


def get_resource_repository(request: Request) -> ResourceRepository:
    return request.app.state.resource_repository


def get_resource_service(request: Request) -> ResourceService:
    return request.app.state.resource_service


def get_saved_view_repository(request: Request) -> SavedViewRepository:
    return request.app.state.saved_view_repository


def get_search_index_repository(request: Request) -> SearchIndexRepository:
    return request.app.state.search_index_repository


def get_saved_view_service(request: Request) -> SavedViewService:
    return request.app.state.saved_view_service


def get_projection_service(request: Request) -> ProjectionService:
    return request.app.state.projection_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_research_dashboard_service(request: Request) -> ResearchDashboardService:
    return request.app.state.research_dashboard_service


def get_research_settings_service(request: Request) -> ResearchSettingsService:
    return request.app.state.research_settings_service


def get_workflow_chain_service(request: Request) -> WorkflowChainService:
    return request.app.state.workflow_chain_service


def get_discovery_service(request: Request) -> DiscoveryService:
    return request.app.state.discovery_service


def get_file_service(request: Request) -> FileService:
    return request.app.state.file_service


def get_activity_service(request: Request) -> ActivityService:
    return request.app.state.activity_service


def get_default_workspace_id(request: Request) -> WorkspaceId:
    return request.app.state.default_workspace_id


def get_default_canvas_id(request: Request) -> CanvasId:
    return request.app.state.default_canvas_id
