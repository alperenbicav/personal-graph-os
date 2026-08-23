"""FastAPI dependency accessors into the composition root stored on `app.state`.

`create_app()` builds one connection/repository/service graph per process and stores it on
`app.state`; these functions just hand pieces of it to route handlers.
"""

from __future__ import annotations

from fastapi import Request

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.agent_service import AgentService
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.clickup_service import ClickupService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.document_service import DocumentService
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.export_service import ExportService
from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.repositories import (
    AgentRepository,
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
from personal_graph_os.application.resource_content_service import ResourceContentService
from personal_graph_os.application.resource_detail_service import ResourceDetailService
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
from personal_graph_os.application.undo_service import UndoService
from personal_graph_os.application.work_item_service import WorkItemService
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


def get_resource_detail_service(request: Request) -> ResourceDetailService:
    return request.app.state.resource_detail_service


def get_resource_content_service(request: Request) -> ResourceContentService:
    return request.app.state.resource_content_service


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


def get_document_service(request: Request) -> DocumentService:
    return request.app.state.document_service


def get_work_item_service(request: Request) -> WorkItemService:
    return request.app.state.work_item_service


def get_file_service(request: Request) -> FileService:
    return request.app.state.file_service


def get_activity_service(request: Request) -> ActivityService:
    return request.app.state.activity_service


def get_undo_service(request: Request) -> UndoService:
    return request.app.state.undo_service


def get_export_service(request: Request) -> ExportService:
    return request.app.state.export_service


def get_extraction_service(request: Request) -> ExtractionService:
    return request.app.state.extraction_service


def get_enrichment_service(request: Request) -> EnrichmentService | None:
    return request.app.state.enrichment_service


def get_capture_planning_orchestrator(request: Request) -> CapturePlanningOrchestrator:
    return request.app.state.capture_planning_orchestrator


def get_clickup_service(request: Request) -> ClickupService:
    return request.app.state.clickup_service


def get_default_workspace_id(request: Request) -> WorkspaceId:
    return request.app.state.default_workspace_id


def get_default_canvas_id(request: Request) -> CanvasId:
    return request.app.state.default_canvas_id


def get_agent_repository(request: Request) -> AgentRepository:
    return request.app.state.agent_repository


def get_agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service
