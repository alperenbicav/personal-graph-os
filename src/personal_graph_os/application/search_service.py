"""Resolves FTS5 hits back into canonical `Node`/`Resource` search results.

`SearchIndexRepository.search()` only knows about indexed text; it has no schema/lifecycle
awareness. `SearchService` is the read-side counterpart to `NodeService`/`ResourceService`'s
indexing: it fetches each hit's canonical `Node` (and `Resource`, when one backs it) so the
default archived-exclusion rule always reflects live state, not whatever was true when the row
was last indexed.
"""

from __future__ import annotations

from personal_graph_os.application.repositories import (
    NodeRepository,
    ResourceRepository,
    SearchIndexRepository,
)
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId
from personal_graph_os.domain.search import SearchResult

# A hit's node/resource is resolved after the FTS5 query runs, so archived nodes (and a stray
# hit whose node was since deleted outside a service, which policy forbids but which must not
# crash a search) may be dropped afterwards. Over-fetching by a fixed multiple keeps that
# post-filtering from starving `limit` without unbounded work.
_OVER_FETCH_MULTIPLE = 4


class SearchService:
    """The read path for full-text search over a workspace's nodes and resources."""

    def __init__(
        self,
        nodes: NodeRepository,
        resources: ResourceRepository,
        search_index: SearchIndexRepository,
    ) -> None:
        self._nodes = nodes
        self._resources = resources
        self._search_index = search_index

    def search(
        self,
        workspace_id: WorkspaceId,
        query_text: str,
        *,
        limit: int = 20,
        include_archived: bool = False,
    ) -> tuple[SearchResult, ...]:
        hits = self._search_index.search(
            workspace_id, query_text, limit=limit * _OVER_FETCH_MULTIPLE
        )

        results: list[SearchResult] = []
        seen_node_ids: set[str] = set()
        for hit in hits:
            if len(results) >= limit:
                break
            if hit.entity_id in seen_node_ids:
                continue
            node = self._nodes.get(NodeId(hit.entity_id))
            if node is None or node.workspace_id != workspace_id:
                continue
            if node.is_archived and not include_archived:
                continue
            seen_node_ids.add(hit.entity_id)
            resource = self._resources.get_by_node(node.id)
            results.append(SearchResult(node=node, resource=resource, snippet=hit.snippet))
        return tuple(results)
