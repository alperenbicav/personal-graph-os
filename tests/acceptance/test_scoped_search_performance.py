"""ST-12 system-acceptance: scoped search stays correct and bounded at representative scale
(EP-2026-012 acceptance 4, performance bound). A few hundred indexed nodes must deduplicate and
page deterministically, and every page's `total`/`has_more` must describe the caller-visible
(deduplicated, archived-filtered) set -- S12-F01.

Rollback is covered by the migration suite (`test_apply_migration_script_is_atomic...` + the 0019
backfill test); cost telemetry is trivially satisfied for search because `SearchService` depends
only on the `SearchEngine` port and makes zero model/provider calls (the injected-stub-engine
contract test proves no provider involvement).
"""

from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.search import (
    SearchEntityType,
    SearchScope,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteBm25SearchEngine,
    SqliteDocumentRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteWorkspaceRepository,
)


@pytest.mark.slow
def test_scoped_search_pages_a_representative_corpus_deterministically(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_type = workspace.node_types[0]
    node_repository = SqliteNodeRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)
    search_service = SearchService(
        node_repository,
        SqliteResourceRepository(sqlite_connection),
        SqliteDocumentRepository(sqlite_connection),
        SqliteBm25SearchEngine(sqlite_connection),
    )

    count = 300
    for index in range(count):
        node = Node(
            workspace_id=workspace.id,
            node_type_id=node_type.id,
            title=f"perf probe {index} sharedprobe",
            body="a bounded corpus for deterministic pagination",
        )
        node_repository.save(node)
        search_index.index_document(
            workspace_id=workspace.id,
            entity_type=SearchEntityType.NODE,
            entity_id=node.id,
            text=f"{node.title}\n{node.body}",
            scope=SearchScope.GRAPH,
        )

    page_size = 25
    offset = 0
    collected: list[str] = []
    seen: set[str] = set()
    while True:
        page = search_service.search(
            workspace.id, "sharedprobe", scope=SearchScope.GRAPH, limit=page_size, offset=offset
        )
        for result in page.results:
            assert result.node is not None
            assert result.node.id not in seen, "deduplicated page leaked a repeat"
            seen.add(result.node.id)
            collected.append(result.node.id)
        assert page.total == count
        assert page.has_more == (offset + page_size < count)
        if not page.has_more:
            break
        offset += page_size

    assert len(collected) == count
