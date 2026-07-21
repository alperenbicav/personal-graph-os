"""Search routes: full-text search over a workspace's nodes/resources via `SearchService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_search_service
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.search import SearchResult

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[SearchResult])
def search(
    workspace_id: str,
    q: str,
    limit: int = 20,
    include_archived: bool = False,
    search_service: SearchService = Depends(get_search_service),
) -> list[SearchResult]:
    return list(
        search_service.search(
            WorkspaceId(workspace_id), q, limit=limit, include_archived=include_archived
        )
    )
