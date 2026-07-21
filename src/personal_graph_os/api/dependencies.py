"""FastAPI dependency accessors into the composition root stored on `app.state`.

`create_app()` builds one connection/repository/service graph per process and stores it on
`app.state`; these functions just hand pieces of it to route handlers.
"""

from __future__ import annotations

from fastapi import Request

from personal_graph_os.application.repositories import (
    CanvasPlacementRepository,
    CanvasRepository,
    EdgeRepository,
    NodeRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.services import (
    CanvasService,
    EdgeService,
    NodeService,
    SchemaService,
)
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


def get_default_workspace_id(request: Request) -> WorkspaceId:
    return request.app.state.default_workspace_id


def get_default_canvas_id(request: Request) -> CanvasId:
    return request.app.state.default_canvas_id
