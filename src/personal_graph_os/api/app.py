"""Composition root: wires SQLite repositories and application services, then exposes them
as a FastAPI app for the local frontend.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from personal_graph_os.api.auth import TOKEN_FILE_NAME, get_or_create_api_token, require_api_token
from personal_graph_os.api.routers import (
    canvases,
    edges,
    nodes,
    placements,
    resources,
    saved_views,
    schema,
    search,
    views,
    workspace,
)
from personal_graph_os.application.bootstrap import (
    backfill_search_index,
    get_or_create_default_canvas,
    get_or_create_default_workspace,
)
from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.services import (
    CanvasNotFoundError,
    CanvasService,
    EdgeService,
    EdgeTypeNotFoundError,
    FieldDefinitionNotFoundError,
    NodeNotFoundError,
    NodeService,
    NodeTypeNotFoundError,
    PlacementNotFoundError,
    ResourceNotFoundError,
    ResourceService,
    SavedViewNotFoundError,
    SavedViewService,
    SchemaService,
    StatusDefinitionNotFoundError,
    WorkspaceNotFoundError,
)
from personal_graph_os.domain.errors import DomainError
from personal_graph_os.infrastructure.sqlite.connection import open_connection
from personal_graph_os.infrastructure.sqlite.migrations.runner import run_migrations
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteCanvasPlacementRepository,
    SqliteCanvasRepository,
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSavedViewRepository,
    SqliteSearchIndexRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = _REPO_ROOT / "workspace" / "graph.db"

_DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    api_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Personal Graph OS API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        token_path = Path(database_path).parent / TOKEN_FILE_NAME
    else:
        token_path = DEFAULT_DATABASE_PATH.parent / TOKEN_FILE_NAME
    app.state.api_token = api_token or get_or_create_api_token(token_path)

    # A single connection is reused for the app's lifetime; endpoint functions run in a
    # thread pool, so cross-thread use must be allowed. `_serialize_requests` below then
    # ensures only one request's database work runs at a time, which is what makes that
    # safe for a local single-user app instead of merely silencing the safety check.
    connection = open_connection(database_path, check_same_thread=False)
    run_migrations(connection)

    workspace_repository = SqliteWorkspaceRepository(connection)
    node_repository = SqliteNodeRepository(connection)
    edge_repository = SqliteEdgeRepository(connection)
    canvas_repository = SqliteCanvasRepository(connection)
    placement_repository = SqliteCanvasPlacementRepository(connection)
    resource_repository = SqliteResourceRepository(connection)
    saved_view_repository = SqliteSavedViewRepository(connection)
    search_index_repository = SqliteSearchIndexRepository(connection)

    default_workspace = get_or_create_default_workspace(workspace_repository)
    default_canvas = get_or_create_default_canvas(canvas_repository, default_workspace)
    backfill_search_index(
        node_repository, resource_repository, search_index_repository, default_workspace.id
    )

    app.state.connection = connection
    app.state.workspace_repository = workspace_repository
    app.state.node_repository = node_repository
    app.state.edge_repository = edge_repository
    app.state.canvas_repository = canvas_repository
    app.state.placement_repository = placement_repository
    app.state.resource_repository = resource_repository
    app.state.saved_view_repository = saved_view_repository
    app.state.search_index_repository = search_index_repository
    app.state.node_service = NodeService(
        workspace_repository, node_repository, search_index=search_index_repository
    )
    app.state.edge_service = EdgeService(workspace_repository, node_repository, edge_repository)
    app.state.canvas_service = CanvasService(
        workspace_repository, node_repository, canvas_repository, placement_repository
    )
    app.state.schema_service = SchemaService(workspace_repository, node_repository, edge_repository)
    app.state.resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.saved_view_service = SavedViewService(workspace_repository, saved_view_repository)
    app.state.projection_service = ProjectionService(node_repository, resource_repository)
    app.state.search_service = SearchService(
        node_repository, resource_repository, search_index_repository
    )
    app.state.default_workspace_id = default_workspace.id
    app.state.default_canvas_id = default_canvas.id
    app.state.db_lock = anyio.Lock()

    @app.middleware("http")
    async def _serialize_requests(request: Request, call_next):
        async with request.app.state.db_lock:
            return await call_next(request)

    auth_dependency = [Depends(require_api_token)]
    app.include_router(workspace.router, dependencies=auth_dependency)
    app.include_router(nodes.router, dependencies=auth_dependency)
    app.include_router(edges.router, dependencies=auth_dependency)
    app.include_router(canvases.router, dependencies=auth_dependency)
    app.include_router(placements.router, dependencies=auth_dependency)
    app.include_router(schema.router, dependencies=auth_dependency)
    app.include_router(resources.router, dependencies=auth_dependency)
    app.include_router(search.router, dependencies=auth_dependency)
    app.include_router(saved_views.router, dependencies=auth_dependency)
    app.include_router(views.router, dependencies=auth_dependency)

    def _not_found(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    def _unprocessable(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    for not_found_error_type in (
        WorkspaceNotFoundError,
        NodeNotFoundError,
        CanvasNotFoundError,
        PlacementNotFoundError,
        NodeTypeNotFoundError,
        FieldDefinitionNotFoundError,
        StatusDefinitionNotFoundError,
        EdgeTypeNotFoundError,
        ResourceNotFoundError,
        SavedViewNotFoundError,
    ):
        app.add_exception_handler(not_found_error_type, _not_found)
    app.add_exception_handler(DomainError, _unprocessable)

    return app
