"""Resolves engine hits back into canonical search results.

`SearchService` depends on the `SearchEngine` read port (EP-2026-012 ST-12), never on SQLite FTS5
directly. The engine returns a deduplicated, archived-filtered, paginated page whose `offset`/
`total`/`has_more` exactly describe the caller-visible entity set (S12-F01); this service resolves
each hit back to its canonical entity (a `Node`+`Resource`, or a `Document` for wiki hits). A tiny
archived safety-net remains for entities archived between the engine query and this resolution.
"""

from __future__ import annotations

from personal_graph_os.application.repositories import (
    DocumentRepository,
    NodeRepository,
    ResourceRepository,
)
from personal_graph_os.domain.identifiers import DocumentId, NodeId, WorkspaceId
from personal_graph_os.domain.search import (
    SearchEngine,
    SearchEntityType,
    SearchHit,
    SearchRequest,
    SearchResult,
    SearchResultPage,
    SearchScope,
)


class SearchService:
    """The read path for scoped, ranked search over a workspace's nodes/resources/documents."""

    def __init__(
        self,
        nodes: NodeRepository,
        resources: ResourceRepository,
        documents: DocumentRepository,
        engine: SearchEngine,
    ) -> None:
        self._nodes = nodes
        self._resources = resources
        self._documents = documents
        self._engine = engine

    def search(
        self,
        workspace_id: WorkspaceId,
        query_text: str,
        *,
        scope: SearchScope = SearchScope.ALL,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> SearchResultPage:
        request = SearchRequest(
            workspace_id=workspace_id,
            query=query_text,
            scope=scope,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
        page = self._engine.search(request)

        results: list[SearchResult] = []
        for hit in page.hits:
            result = self._resolve_hit(workspace_id, hit, include_archived=include_archived)
            if result is None:
                continue
            results.append(result)
        return SearchResultPage(
            results=tuple(results),
            total=page.total,
            has_more=page.has_more,
            offset=offset,
        )

    def _resolve_hit(
        self, workspace_id: WorkspaceId, hit: SearchHit, *, include_archived: bool
    ) -> SearchResult | None:
        if hit.entity_type is SearchEntityType.DOCUMENT:
            document = self._documents.get(DocumentId(hit.entity_id))
            if document is None or document.workspace_id != workspace_id:
                return None
            if document.is_archived and not include_archived:
                return None
            return SearchResult(
                node=None,
                resource=None,
                document=document,
                snippet=hit.snippet,
                score=hit.score,
                scope=hit.scope,
                entity_type=hit.entity_type,
                source=document.source,
                goto=hit.entity_id,
            )

        node = self._nodes.get(NodeId(hit.entity_id))
        if node is None or node.workspace_id != workspace_id:
            return None
        if node.is_archived and not include_archived:
            return None
        resource = self._resources.get_by_node(node.id)
        return SearchResult(
            node=node,
            resource=resource,
            document=None,
            snippet=hit.snippet,
            score=hit.score,
            scope=hit.scope,
            entity_type=hit.entity_type,
            source=resource.source_url if resource is not None else None,
            goto=hit.entity_id,
        )
