from __future__ import annotations

import sqlite3

from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.domain.documents import Document, DocumentKind, DocumentVersion
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.resource import Resource, ResourceKind
from personal_graph_os.domain.search import (
    SearchEntityType,
    SearchHit,
    SearchPage,
    SearchRequest,
    SearchScope,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteBm25SearchEngine,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
    SqliteSearchIndexRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    document_repository = SqliteDocumentRepository(sqlite_connection)
    document_version_repository = SqliteDocumentVersionRepository(sqlite_connection)
    search_index = SqliteSearchIndexRepository(sqlite_connection)
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    resource_service = ResourceService(
        workspace_repository, resource_repository, unit_of_work_factory, search_index=search_index
    )
    search_service = SearchService(
        node_repository,
        resource_repository,
        document_repository,
        SqliteBm25SearchEngine(sqlite_connection),
    )
    return {
        "workspace": workspace,
        "node_repository": node_repository,
        "resource_repository": resource_repository,
        "document_repository": document_repository,
        "document_version_repository": document_version_repository,
        "search_index": search_index,
        "resource_service": resource_service,
        "search_service": search_service,
    }


def _seed_objects(ctx, sqlite_connection: sqlite3.Connection) -> dict[str, str]:
    """Index a generic graph node, an article resource, a repository resource, a work item's
    node, and a wiki document -- each with a distinct token so scope filtering is observable."""
    workspace = ctx["workspace"]
    workspace_id: WorkspaceId = workspace.id
    node_repository: SqliteNodeRepository = ctx["node_repository"]
    resource_repository: SqliteResourceRepository = ctx["resource_repository"]
    search_index: SqliteSearchIndexRepository = ctx["search_index"]
    document_repository: SqliteDocumentRepository = ctx["document_repository"]
    document_version_repository: SqliteDocumentVersionRepository = ctx[
        "document_version_repository"
    ]

    node_type = workspace.node_types[0]
    generic_node = Node(
        workspace_id=workspace_id,
        node_type_id=node_type.id,
        title="graphscope generic note",
        body="only the graph scope should find this scopetest",
    )
    node_repository.save(generic_node)
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id=generic_node.id,
        text=generic_node.title + "\n" + generic_node.body,
        scope=SearchScope.GRAPH,
    )

    article_node = Node(
        workspace_id=workspace_id,
        node_type_id=node_type.id,
        title="researchscope article",
        body="research scope only scopetest",
    )
    node_repository.save(article_node)
    article = Resource(
        workspace_id=workspace_id,
        node_id=article_node.id,
        kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/researchtoken-article",
        source_url="https://example.com/researchtoken-article",
    )
    resource_repository.save(article)
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id=article_node.id,
        text=article_node.title + "\n" + article_node.body,
        scope=SearchScope.RESEARCH,
    )
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.RESOURCE,
        entity_id=article_node.id,
        text="researchscope identity scopetest",
        scope=SearchScope.RESEARCH,
    )

    repo_node = Node(
        workspace_id=workspace_id,
        node_type_id=node_type.id,
        title="reposcope repository",
        body="repositories scope only scopetest",
    )
    node_repository.save(repo_node)
    repo = Resource(
        workspace_id=workspace_id,
        node_id=repo_node.id,
        kind=ResourceKind.GITHUB_REPOSITORY,
        canonical_identifier="github:owner/repo",
        source_url="https://github.com/owner/repo",
    )
    resource_repository.save(repo)
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id=repo_node.id,
        text=repo_node.title + "\n" + repo_node.body,
        scope=SearchScope.REPOSITORIES,
    )
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.RESOURCE,
        entity_id=repo_node.id,
        text="reposcope identity scopetest",
        scope=SearchScope.REPOSITORIES,
    )

    work_item_node = Node(
        workspace_id=workspace_id,
        node_type_id=node_type.id,
        title="taskscope work item",
        body="tasks scope only scopetest",
    )
    node_repository.save(work_item_node)
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id=work_item_node.id,
        text=work_item_node.title + "\n" + work_item_node.body,
        scope=SearchScope.TASKS,
    )

    document = Document(
        workspace_id=workspace_id,
        kind=DocumentKind.NOTE,
        title="wikiscope document",
        source="manual",
    )
    document_repository.save(document)
    document_version_repository.save_without_commit(
        DocumentVersion(
            document_id=document.id,
            version_number=1,
            body_markdown="wiki scope only body scopetest",
            created_by="test",
        )
    )
    search_index.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.DOCUMENT,
        entity_id=document.id,
        text=document.title + "\nwiki scope only body scopetest",
        scope=SearchScope.WIKI,
    )

    return {
        "generic_node_id": generic_node.id,
        "article_node_id": article_node.id,
        "repo_node_id": repo_node.id,
        "work_item_node_id": work_item_node.id,
        "document_id": document.id,
    }


def _search_titles(
    search_service: SearchService, workspace_id: WorkspaceId, query: str, scope
) -> tuple[str, ...]:
    return tuple(
        result.node.title
        if result.node is not None
        else (result.document.title if result.document else "")
        for result in search_service.search(workspace_id, query, scope=scope).results
    )


def test_all_scope_returns_every_tab(sqlite_connection: sqlite3.Connection) -> None:
    ctx = _fixture(sqlite_connection)
    _seed_objects(ctx, sqlite_connection)
    workspace_id: WorkspaceId = ctx["workspace"].id

    titles = _search_titles(ctx["search_service"], workspace_id, "scopetest", SearchScope.ALL)

    assert "graphscope generic note" in titles
    assert "researchscope article" in titles
    assert "reposcope repository" in titles
    assert "taskscope work item" in titles
    assert "wikiscope document" in titles


def test_each_scope_returns_only_its_tab(sqlite_connection: sqlite3.Connection) -> None:
    ctx = _fixture(sqlite_connection)
    _seed_objects(ctx, sqlite_connection)
    workspace_id: WorkspaceId = ctx["workspace"].id

    assert _search_titles(ctx["search_service"], workspace_id, "graphscope", SearchScope.GRAPH) == (
        "graphscope generic note",
    )
    assert _search_titles(
        ctx["search_service"], workspace_id, "researchscope", SearchScope.RESEARCH
    ) == ("researchscope article",)
    assert _search_titles(
        ctx["search_service"], workspace_id, "reposcope", SearchScope.REPOSITORIES
    ) == ("reposcope repository",)
    assert _search_titles(ctx["search_service"], workspace_id, "taskscope", SearchScope.TASKS) == (
        "taskscope work item",
    )
    assert _search_titles(ctx["search_service"], workspace_id, "wikiscope", SearchScope.WIKI) == (
        "wikiscope document",
    )


def test_wiki_scope_resolves_to_the_document_and_remove_deletes_the_row(
    sqlite_connection: sqlite3.Connection,
) -> None:
    ctx = _fixture(sqlite_connection)
    ids = _seed_objects(ctx, sqlite_connection)
    workspace_id: WorkspaceId = ctx["workspace"].id
    search_service: SearchService = ctx["search_service"]

    results = search_service.search(workspace_id, "wikiscope", scope=SearchScope.WIKI).results
    assert len(results) == 1
    result = results[0]
    assert result.document is not None
    assert result.document.id == ids["document_id"]
    assert result.node is None
    assert result.scope is SearchScope.WIKI
    assert result.goto == ids["document_id"]

    ctx["search_index"].remove_document(
        entity_type=SearchEntityType.DOCUMENT, entity_id=ids["document_id"]
    )
    assert search_service.search(workspace_id, "wikiscope", scope=SearchScope.WIKI).results == ()


def test_pagination_is_deterministic_and_windows_correctly(
    sqlite_connection: sqlite3.Connection,
) -> None:
    ctx = _fixture(sqlite_connection)
    _seed_objects(ctx, sqlite_connection)
    workspace_id: WorkspaceId = ctx["workspace"].id
    engine = SqliteBm25SearchEngine(sqlite_connection)

    page_one = engine.search(
        SearchRequest(
            workspace_id=workspace_id, query="scopetest", scope=SearchScope.ALL, limit=2, offset=0
        )
    )
    page_two = engine.search(
        SearchRequest(
            workspace_id=workspace_id, query="scopetest", scope=SearchScope.ALL, limit=2, offset=2
        )
    )

    # The engine dedups by entity (a resource's node+resource rows collapse to one), so total is
    # the 5 distinct entities and pages are disjoint on entity id (S12-F01).
    assert page_one.total == 5
    assert page_one.has_more is True
    assert len(page_one.hits) == 2
    assert len(page_two.hits) == 2
    assert page_two.has_more is True
    page_one_entities = {h.entity_id for h in page_one.hits}
    assert all(h.entity_id not in page_one_entities for h in page_two.hits)


class _StubEngine:
    """A second `SearchEngine` implementation: `SearchService` must behave identically when the
    engine is swapped (acceptance 1's extension seam), so this stub feeds a fixed page."""

    def __init__(self, page: SearchPage) -> None:
        self._page = page

    def search(self, request: SearchRequest) -> SearchPage:
        return self._page


def test_an_injected_engine_leaves_service_behavior_identical(
    sqlite_connection: sqlite3.Connection,
) -> None:
    ctx = _fixture(sqlite_connection)
    workspace_id: WorkspaceId = ctx["workspace"].id
    node_type = ctx["workspace"].node_types[0]
    node = Node(workspace_id=workspace_id, node_type_id=node_type.id, title="stub hit")
    ctx["node_repository"].save(node)
    hit = SearchHit(
        entity_type=SearchEntityType.NODE,
        entity_id=node.id,
        snippet="[stub] hit",
        score=1.0,
        scope=SearchScope.GRAPH,
    )
    stub_engine = _StubEngine(SearchPage(hits=(hit,), total=1, has_more=False))
    service = SearchService(
        ctx["node_repository"],
        ctx["resource_repository"],
        ctx["document_repository"],
        stub_engine,
    )

    results = service.search(workspace_id, "stub", scope=SearchScope.GRAPH).results

    assert len(results) == 1
    assert results[0].node is not None
    assert results[0].node.title == "stub hit"
    assert results[0].snippet == "[stub] hit"
    assert results[0].score == 1.0
