"""Search routes: scoped, ranked full-text search over a workspace's nodes/resources/documents.

Supports the ST-12 scope selector (`all`/`wiki`/`tasks`/`research`/`repositories`/`graph`),
deterministic `limit`/`offset` pagination surfaced via `total`/`has_more` (S12-F01), and the
enriched result projection (score, excerpt, entity type, source, and a canonical deep-link `goto`
id).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_search_service
from personal_graph_os.api.schemas import SearchResponse
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.search import SearchScope

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse)
def search(
    workspace_id: str,
    q: str,
    scope: SearchScope = SearchScope.ALL,
    limit: int = 20,
    offset: int = 0,
    include_archived: bool = False,
    search_service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    page = search_service.search(
        WorkspaceId(workspace_id),
        q,
        scope=scope,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )
    return SearchResponse(
        results=list(page.results),
        total=page.total,
        has_more=page.has_more,
        offset=page.offset,
    )
