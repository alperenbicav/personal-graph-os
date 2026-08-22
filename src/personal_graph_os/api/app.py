"""Composition root: wires SQLite repositories and application services, then exposes them
as a FastAPI app for the local frontend.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from personal_graph_os.api.auth import TOKEN_FILE_NAME, get_or_create_api_token, require_api_token
from personal_graph_os.api.routers import (
    activity,
    canvases,
    capture,
    clickup,
    discovery,
    edges,
    enrichment,
    export,
    files,
    nodes,
    placements,
    research,
    resources,
    saved_views,
    schema,
    search,
    views,
    wiki,
    work_items,
    workflow_chain,
    workspace,
)
from personal_graph_os.application.activity_service import (
    ActivityEventNotFoundError,
    ActivityService,
    InvalidActivityCursorError,
)
from personal_graph_os.application.bootstrap import (
    backfill_search_index,
    get_or_create_default_canvas,
    get_or_create_default_workspace,
)
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.clickup_adapters import (
    ClickUpAccessDeniedError,
    ClickUpFetchFailedError,
    ClickUpNotConfiguredError,
    ClickUpTaskNotFoundError,
)
from personal_graph_os.application.clickup_service import ClickupService
from personal_graph_os.application.context_pack_service import ContextPackService
from personal_graph_os.application.discovery import DiscoveryService
from personal_graph_os.application.document_service import (
    CollectionNotFoundError,
    DocumentLinkNotFoundError,
    DocumentLinkTargetNotFoundError,
    DocumentNotFoundError,
    DocumentService,
    TagNotFoundError,
)
from personal_graph_os.application.enrichment_service import (
    EnrichmentNotConfiguredError,
    EnrichmentService,
)
from personal_graph_os.application.export_service import ExportService
from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.application.file_service import (
    AttachmentNotFoundError,
    FileReferenceNotFoundError,
    FileService,
)
from personal_graph_os.application.file_service import (
    NodeNotFoundError as FileServiceNodeNotFoundError,
)
from personal_graph_os.application.projections import ProjectionService
from personal_graph_os.application.research_dashboard import ResearchDashboardService
from personal_graph_os.application.resource_content_service import ResourceContentNotFoundError
from personal_graph_os.application.resource_detail_service import ResourceDetailService
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
    ResearchSettingsService,
    ResourceNotFoundError,
    ResourceService,
    SavedViewNotFoundError,
    SavedViewService,
    SchemaService,
    StatusDefinitionNotFoundError,
    WorkspaceNotFoundError,
)
from personal_graph_os.application.telegram_poller import TelegramPoller
from personal_graph_os.application.telegram_service import TelegramService
from personal_graph_os.application.undo_service import UndoConflictError, UndoService
from personal_graph_os.application.work_item_service import (
    WorkItemChecklistItemNotFoundError,
    WorkItemNotFoundError,
    WorkItemService,
)
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.application.workflow_chain import (
    WorkflowChainService,
    WorkflowStepNodeTypeMissingError,
)
from personal_graph_os.domain.capture import CaptureIdempotencyConflictError
from personal_graph_os.domain.errors import (
    AttachmentContentCorruptedError,
    AttachmentContentMissingError,
    DomainError,
    UploadTooLargeError,
)
from personal_graph_os.infrastructure.agent.agent_loop import AgentLoopService
from personal_graph_os.infrastructure.agent.provider_factory import (
    build_agent_chat_provider_from_env,
)
from personal_graph_os.infrastructure.clickup.builder import build_clickup_client_from_env
from personal_graph_os.infrastructure.enrichment.provider_factory import (
    build_enrichment_provider_from_env,
)
from personal_graph_os.infrastructure.extraction.arxiv_metadata_adapter import ArxivMetadataAdapter
from personal_graph_os.infrastructure.extraction.docling_pdf_parser import DoclingPdfParser
from personal_graph_os.infrastructure.extraction.doi_metadata_adapter import DoiMetadataAdapter
from personal_graph_os.infrastructure.extraction.github_metadata_adapter import (
    GitHubMetadataAdapter,
)
from personal_graph_os.infrastructure.extraction.html_article_parser import HtmlArticleParser
from personal_graph_os.infrastructure.extraction.http_content_fetcher import HttpContentFetcher
from personal_graph_os.infrastructure.extraction.pdf_text import extract_pdf_text
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.mcp.auth import with_bearer_token
from personal_graph_os.infrastructure.mcp.gateway import AgentGatewayService
from personal_graph_os.infrastructure.mcp.server import create_mcp_asgi_app
from personal_graph_os.infrastructure.sqlite.connection import open_connection
from personal_graph_os.infrastructure.sqlite.migrations.runner import run_migrations
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteAttachmentRepository,
    SqliteBm25SearchEngine,
    SqliteCanvasPlacementRepository,
    SqliteCanvasRepository,
    SqliteCollectionRepository,
    SqliteContextPackRepository,
    SqliteDiscoveryRunRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteResearchSettingsRepository,
    SqliteResourceEnrichmentProfileRepository,
    SqliteResourceEnrichmentProfileVersionRepository,
    SqliteResourceRepository,
    SqliteSavedViewRepository,
    SqliteSearchIndexRepository,
    SqliteTagRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.telegram.builder import build_telegram_client_from_env
from personal_graph_os.infrastructure.work_planning.provider_factory import (
    build_work_planning_provider_from_env,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = _REPO_ROOT / "workspace" / "graph.db"
MANAGED_FILES_DIR_NAME = "managed-files"
EXPORT_TEMP_DIR_NAME = ".export-tmp"

DEFAULT_TRUSTED_HOSTS = ["127.0.0.1", "localhost"]
DEFAULT_STATIC_DIR = _REPO_ROOT / "frontend" / "dist"
DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
STATIC_MOUNT_PATH = "/app"
_STATIC_ASSET_PREFIX = "assets/"
# Telegram long-poll runs up to `poll_timeout_seconds` (25s) plus idle sleep per iteration, so
# the shutdown join must allow one in-flight poll to finish.
_TELEGRAM_POLLER_JOIN_TIMEOUT_SECONDS = 30.0


class _AppStaticFiles(StaticFiles):
    """Serves the built frontend under `STATIC_MOUNT_PATH`. Hashed files under `assets/`
    are safe to cache forever; the HTML shell, manifest, and generated service worker must
    always revalidate so a browser can never keep serving a stale entrypoint after a new
    build lands (08.2's update-prompt flow depends on this).
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            if path.startswith(_STATIC_ASSET_PREFIX):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    api_token: str | None = None,
    trusted_hosts: list[str] | None = None,
    cors_origins: list[str] | None = None,
    static_dir: Path | str | None = None,
    enrichment_provider_transport: httpx.BaseTransport | None = None,
    work_planning_provider_transport: httpx.BaseTransport | None = None,
    clickup_transport: httpx.BaseTransport | None = None,
    telegram_transport: httpx.BaseTransport | None = None,
    agent_chat_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    app = FastAPI(title="Personal Graph OS API")

    # Both default to permissive/absent so `create_app(tmp_path)` keeps working for the
    # existing test suite and any programmatic embedding without a frontend build; the real
    # server (`api/__main__.py`) opts into an explicit host allowlist and never enables CORS
    # for its production same-origin posture.
    if trusted_hosts is not None:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(trusted_hosts))

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        workspace_dir = Path(database_path).parent
    else:
        workspace_dir = DEFAULT_DATABASE_PATH.parent
    token_path = workspace_dir / TOKEN_FILE_NAME
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
    context_pack_repository = SqliteContextPackRepository(connection)
    discovery_run_repository = SqliteDiscoveryRunRepository(connection)
    saved_view_repository = SqliteSavedViewRepository(connection)
    search_index_repository = SqliteSearchIndexRepository(connection)
    research_settings_repository = SqliteResearchSettingsRepository(connection)
    attachment_repository = SqliteAttachmentRepository(connection)
    file_reference_repository = SqliteFileReferenceRepository(connection)
    pending_file_operation_repository = SqlitePendingFileOperationRepository(connection)
    activity_event_repository = SqliteActivityEventRepository(connection)
    ingestion_job_repository = SqliteIngestionJobRepository(connection)
    idempotency_receipt_repository = SqliteIdempotencyReceiptRepository(connection)
    work_item_repository = SqliteWorkItemRepository(connection)
    document_repository = SqliteDocumentRepository(connection)
    document_version_repository = SqliteDocumentVersionRepository(connection)
    document_link_repository = SqliteDocumentLinkRepository(connection)
    collection_repository = SqliteCollectionRepository(connection)
    tag_repository = SqliteTagRepository(connection)
    resource_enrichment_profile_repository = SqliteResourceEnrichmentProfileRepository(connection)
    resource_enrichment_profile_version_repository = (
        SqliteResourceEnrichmentProfileVersionRepository(connection)
    )
    managed_file_store = LocalManagedFileStore(workspace_dir / MANAGED_FILES_DIR_NAME)

    default_workspace = get_or_create_default_workspace(workspace_repository)
    default_canvas = get_or_create_default_canvas(canvas_repository, default_workspace)
    backfill_search_index(
        node_repository,
        resource_repository,
        work_item_repository,
        search_index_repository,
        default_workspace,
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
    app.state.activity_event_repository = activity_event_repository
    app.state.file_service = FileService(
        node_repository,
        attachment_repository,
        file_reference_repository,
        managed_file_store,
        pending_file_operation_repository,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    app.state.file_service.reconcile_pending_operations()
    app.state.node_service = NodeService(
        workspace_repository,
        node_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.edge_service = EdgeService(
        workspace_repository,
        node_repository,
        edge_repository,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    app.state.canvas_service = CanvasService(
        workspace_repository,
        node_repository,
        canvas_repository,
        placement_repository,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    app.state.schema_service = SchemaService(
        workspace_repository,
        node_repository,
        edge_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.document_service = DocumentService(
        workspace_repository,
        document_repository,
        document_version_repository,
        document_link_repository,
        collection_repository,
        tag_repository,
        node_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.resource_detail_service = ResourceDetailService(
        workspace_repository,
        resource_repository,
        node_repository,
        edge_repository,
        document_link_repository,
        document_repository,
        resource_enrichment_profile_repository,
        resource_enrichment_profile_version_repository,
        ingestion_job_repository,
        work_item_repository,
    )
    app.state.saved_view_service = SavedViewService(
        workspace_repository, saved_view_repository, lambda: SqliteResearchUnitOfWork(connection)
    )
    app.state.projection_service = ProjectionService(node_repository, resource_repository)
    app.state.search_service = SearchService(
        node_repository,
        resource_repository,
        document_repository,
        SqliteBm25SearchEngine(connection),
    )
    app.state.research_dashboard_service = ResearchDashboardService(
        workspace_repository, resource_repository, edge_repository, research_settings_repository
    )
    app.state.research_settings_service = ResearchSettingsService(
        workspace_repository,
        research_settings_repository,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    app.state.workflow_chain_service = WorkflowChainService(
        workspace_repository, node_repository, lambda: SqliteResearchUnitOfWork(connection)
    )
    app.state.discovery_service = DiscoveryService(
        workspace_repository,
        resource_repository,
        app.state.resource_service,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    app.state.activity_service = ActivityService(activity_event_repository)
    app.state.undo_service = UndoService(
        activity_event_repository,
        node_repository,
        resource_repository,
        edge_repository,
        placement_repository,
        saved_view_repository,
        research_settings_repository,
        lambda: SqliteResearchUnitOfWork(connection),
        node_service=app.state.node_service,
        resource_service=app.state.resource_service,
    )

    def _list_activity_events_page(workspace_id, limit, cursor):
        page = app.state.activity_service.list_workspace_events(
            workspace_id, limit=limit, cursor=cursor
        )
        return list(page.events), page.next_cursor

    content_fetcher = HttpContentFetcher()
    app.state.extraction_service = ExtractionService(
        metadata_adapters=(
            DoiMetadataAdapter(content_fetcher),
            ArxivMetadataAdapter(content_fetcher),
            GitHubMetadataAdapter(content_fetcher),
        ),
        content_parsers=(HtmlArticleParser(), DoclingPdfParser()),
        content_fetcher=content_fetcher,
    )
    enrichment_provider = build_enrichment_provider_from_env(
        os.environ, transport=enrichment_provider_transport
    )
    app.state.enrichment_service = (
        EnrichmentService(
            workspace_repository, enrichment_provider, lambda: SqliteResearchUnitOfWork(connection)
        )
        if enrichment_provider is not None
        else None
    )

    capture_service = CaptureService(
        workspace_repository,
        ingestion_job_repository,
        idempotency_receipt_repository,
        app.state.resource_service,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    work_item_service = WorkItemService(
        workspace_repository,
        work_item_repository,
        SqliteWorkItemChecklistItemRepository(connection),
        lambda: SqliteResearchUnitOfWork(connection),
        search_index=search_index_repository,
    )
    app.state.work_item_service = work_item_service
    work_planning_provider = build_work_planning_provider_from_env(
        os.environ, transport=work_planning_provider_transport
    )
    work_planning_service = (
        WorkPlanningService(
            work_item_service,
            work_planning_provider,
            lambda: SqliteResearchUnitOfWork(connection),
            search_index=search_index_repository,
        )
        if work_planning_provider is not None
        else None
    )
    app.state.capture_planning_orchestrator = CapturePlanningOrchestrator(
        capture_service,
        work_planning_service,
        lambda: SqliteResearchUnitOfWork(connection),
        enrichment_service=app.state.enrichment_service,
        extraction_service=app.state.extraction_service,
    )
    clickup_client = build_clickup_client_from_env(os.environ, transport=clickup_transport)
    app.state.clickup_service = ClickupService(
        clickup_client,
        app.state.capture_planning_orchestrator,
        workspace_repository,
        lambda: SqliteResearchUnitOfWork(connection),
    )
    telegram_client = build_telegram_client_from_env(os.environ, transport=telegram_transport)
    app.state.telegram_poller = None
    app.state.telegram_service = TelegramService(
        telegram_client,
        app.state.capture_planning_orchestrator,
        default_workspace.id,
        lambda: SqliteResearchUnitOfWork(connection),
    )

    app.state.export_service = ExportService(
        workspace_repository,
        node_repository,
        edge_repository,
        canvas_repository,
        placement_repository,
        resource_repository,
        saved_view_repository,
        context_pack_repository,
        research_settings_repository,
        discovery_run_repository,
        attachment_repository,
        file_reference_repository,
        app.state.file_service,
        _list_activity_events_page,
        workspace_dir / EXPORT_TEMP_DIR_NAME,
    )
    app.state.context_pack_service = ContextPackService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        app.state.file_service,
        context_pack_repository,
    )
    app.state.default_workspace_id = default_workspace.id
    app.state.default_canvas_id = default_canvas.id
    app.state.db_lock = anyio.Lock()

    @app.middleware("http")
    async def _serialize_requests(request: Request, call_next):
        # `/mcp` is exempt here (ST06-F05): a Streamable HTTP session can sit open and idle
        # between tool calls, and wrapping the whole request would hold this lock across that
        # idle time plus protocol negotiation, blocking every REST request meanwhile. MCP
        # instead locks only the narrow gateway/database call inside each tool/resource
        # dispatch (`build_mcp_server`, same `db_lock`), so REST and MCP still serialize their
        # actual database execution without serializing each other's non-DB time.
        if request.url.path == "/mcp":
            return await call_next(request)
        async with request.app.state.db_lock:
            return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # Every response, static or dynamic, gets a small fixed set of browser hardening
        # headers. Non-static responses additionally get `no-store`: nothing under the
        # bearer-authenticated API/MCP/export surface is safe for a shared or disk cache,
        # unlike the public build assets `_AppStaticFiles` serves under `STATIC_MOUNT_PATH`.
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(f"{STATIC_MOUNT_PATH}/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    agent_gateway = AgentGatewayService(
        workspace_repository,
        node_repository,
        edge_repository,
        resource_repository,
        app.state.search_service,
        app.state.file_service,
        node_service=app.state.node_service,
        edge_service=app.state.edge_service,
        resource_service=app.state.resource_service,
        workflow_chain_service=app.state.workflow_chain_service,
        discovery_service=app.state.discovery_service,
        context_pack_service=app.state.context_pack_service,
        activity_service=app.state.activity_service,
        document_service=app.state.document_service,
        work_item_service=app.state.work_item_service,
        enrichment_service=app.state.enrichment_service,
        extraction_service=app.state.extraction_service,
        capture_planning_orchestrator=app.state.capture_planning_orchestrator,
        clickup_service=app.state.clickup_service,
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(connection),
    )

    # The Telegram agent bot (EP-2026-012 follow-up): a tool-calling chat provider wired into
    # the same `AgentGatewayService` the MCP server exposes. Fail-closed -- without a configured
    # provider the channel keeps its plain URL-capture behavior (agent_loop stays None).
    agent_chat_provider = build_agent_chat_provider_from_env(
        os.environ, transport=agent_chat_transport
    )
    agent_loop = (
        AgentLoopService(
            provider=agent_chat_provider,
            gateway=agent_gateway,
            workspace_id=default_workspace.id,
        )
        if agent_chat_provider is not None
        else None
    )
    # The poller runs on its own daemon thread, so it gets its own SQLite connection: sharing
    # the request connection would let the thread's BEGIN/commit interleave with a request's
    # transaction on the same handle (the `db_lock` is an anyio.Lock a foreign thread cannot
    # acquire). WAL + `busy_timeout` serialize cross-connection writers at the database level.
    telegram_connection: sqlite3.Connection | None = (
        open_connection(database_path, check_same_thread=False)
        if telegram_client is not None
        else None
    )

    def _telegram_unit_of_work() -> SqliteResearchUnitOfWork:
        assert telegram_connection is not None  # only built when the client exists
        return SqliteResearchUnitOfWork(telegram_connection)

    app.state.telegram_connection = telegram_connection
    app.state.telegram_service = TelegramService(
        telegram_client,
        app.state.capture_planning_orchestrator,
        default_workspace.id,
        _telegram_unit_of_work,
        agent_loop=agent_loop,
        pdf_text_extractor=extract_pdf_text,
    )
    app.state.telegram_poller = (
        TelegramPoller(telegram_client, app.state.telegram_service)
        if telegram_client is not None
        else None
    )
    mcp_asgi_app, mcp_session_manager, app.state.mcp_telemetry = create_mcp_asgi_app(
        agent_gateway, db_lock=app.state.db_lock, connection=connection
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # The MCP session manager owns its own task group for the process lifetime.
        # ST-11: when Telegram is configured (kill switch + token + allowlist), a daemon thread
        # long-polls the bot for the app lifetime; it never starts when unconfigured and stops
        # cleanly on shutdown via the stop flag.
        telegram_stop = threading.Event()
        telegram_thread: threading.Thread | None = None
        if app.state.telegram_poller is not None:
            poller = app.state.telegram_poller

            def _run_poller() -> None:
                poller.run(should_stop=lambda: telegram_stop.is_set())

            telegram_thread = threading.Thread(
                target=_run_poller, name="telegram-poller", daemon=True
            )
            telegram_thread.start()
        try:
            async with mcp_session_manager.run():
                yield
        finally:
            if telegram_thread is not None:
                telegram_stop.set()
                telegram_thread.join(timeout=_TELEGRAM_POLLER_JOIN_TIMEOUT_SECONDS)
            if app.state.telegram_connection is not None:
                app.state.telegram_connection.close()

    app.router.lifespan_context = _lifespan
    # A plain Starlette `Route` wrapping a raw ASGI app, not `Mount`: `Mount`'s path pattern
    # always requires a trailing slash to match, so an exact `/mcp` request (no trailing
    # slash) would 307-redirect before the bearer check ever ran — a real MCP client (httpx
    # without follow_redirects) would see the redirect, not the actual protocol response.
    # FastAPI's typed `add_route` only accepts a `Request`-handling endpoint, so the route is
    # constructed directly and appended to the router instead.
    app.router.routes.append(
        Route(
            "/mcp",
            with_bearer_token(mcp_asgi_app, lambda: app.state.api_token),
            methods=["GET", "POST", "DELETE"],
        )
    )

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
    app.include_router(research.router, dependencies=auth_dependency)
    app.include_router(workflow_chain.router, dependencies=auth_dependency)
    app.include_router(discovery.router, dependencies=auth_dependency)
    app.include_router(files.router, dependencies=auth_dependency)
    app.include_router(activity.router, dependencies=auth_dependency)
    app.include_router(export.router, dependencies=auth_dependency)
    app.include_router(enrichment.router, dependencies=auth_dependency)
    app.include_router(capture.router, dependencies=auth_dependency)
    app.include_router(clickup.router, dependencies=auth_dependency)
    app.include_router(wiki.router, dependencies=auth_dependency)
    app.include_router(work_items.router, dependencies=auth_dependency)
    app.include_router(work_items.checklist_router, dependencies=auth_dependency)

    if static_dir is not None:
        # A distinct `/app` prefix, mounted after every API router: it cannot shadow `/mcp`
        # or any REST path above, and needs no bearer auth of its own (the served shell is
        # public; the runtime-authenticated session lives in the browser, not the build).
        app.mount(
            STATIC_MOUNT_PATH,
            _AppStaticFiles(directory=static_dir, html=True),
            name="app-static",
        )

    def _not_found(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    def _unprocessable(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def _too_large(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    def _conflict(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def _enrichment_not_configured(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def _clickup_not_configured(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def _clickup_access_denied(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    def _clickup_gateway_error(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    def _undo_conflict(_request: Request, exc: Exception) -> JSONResponse:
        # A stable machine-readable `code` alongside `detail` (ST07-F05 re-review): callers
        # and the UI can distinguish policy-disabled undo from a snapshot-bound failure without
        # parsing the human-readable message.
        assert isinstance(exc, UndoConflictError)
        return JSONResponse(status_code=409, content={"detail": str(exc), "code": exc.code})

    app.add_exception_handler(UploadTooLargeError, _too_large)
    app.add_exception_handler(AttachmentContentCorruptedError, _conflict)
    app.add_exception_handler(CaptureIdempotencyConflictError, _conflict)
    app.add_exception_handler(UndoConflictError, _undo_conflict)
    app.add_exception_handler(InvalidActivityCursorError, _unprocessable)
    app.add_exception_handler(EnrichmentNotConfiguredError, _enrichment_not_configured)
    app.add_exception_handler(ClickUpNotConfiguredError, _clickup_not_configured)
    app.add_exception_handler(ClickUpAccessDeniedError, _clickup_access_denied)
    app.add_exception_handler(ClickUpFetchFailedError, _clickup_gateway_error)

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
        ResourceContentNotFoundError,
        SavedViewNotFoundError,
        WorkflowStepNodeTypeMissingError,
        FileServiceNodeNotFoundError,
        AttachmentNotFoundError,
        FileReferenceNotFoundError,
        AttachmentContentMissingError,
        ActivityEventNotFoundError,
        DocumentNotFoundError,
        CollectionNotFoundError,
        TagNotFoundError,
        DocumentLinkNotFoundError,
        DocumentLinkTargetNotFoundError,
        WorkItemNotFoundError,
        WorkItemChecklistItemNotFoundError,
        ClickUpTaskNotFoundError,
    ):
        app.add_exception_handler(not_found_error_type, _not_found)
    app.add_exception_handler(DomainError, _unprocessable)

    return app
