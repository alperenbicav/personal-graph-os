"""Full-text search over node/resource text: safe query compilation and result shape.

Indexing and the FTS5 storage itself are an infrastructure concern (`SqliteSearchIndexRepository`);
this module owns the one thing that must stay provider-agnostic and independently testable: turning
arbitrary user input into a query FTS5 can execute safely, plus the typed hit/result shapes callers
work with.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from personal_graph_os.domain.documents import Document
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.resource import Resource, ResourceKind
from personal_graph_os.domain.schema import FieldType, NodeType


def build_node_search_text(node: Node, node_type: NodeType) -> str:
    """The text `NodeService` indexes for a node: its own title/body plus every schema-driven
    field whose type is intentionally searchable free text.

    Only `FieldType.TEXT` counts: a `select`/`url`/`file_path`/`object_reference`/`date`/
    `number`/`boolean` value is not free text a user typed to be found by, and indexing it
    (especially an internal node id behind `object_reference`) would leak structure beyond the
    approved search contract rather than serve relevance.
    """
    text_field_ids = {
        field.id for field in node_type.field_definitions if field.field_type is FieldType.TEXT
    }
    field_texts = (
        str(value)
        for field_id, value in node.field_values.items()
        if field_id in text_field_ids and value is not None
    )
    return "\n".join((node.title, node.body, *field_texts))


def build_resource_search_text(resource: Resource) -> str:
    """The text `ResourceService` indexes for a resource's identity/kind/takeaways/questions."""
    return "\n".join(
        (
            resource.kind.value,
            resource.canonical_identifier,
            resource.source_url or "",
            *resource.takeaways,
            *resource.open_questions,
        )
    )


class SearchEntityType(StrEnum):
    """Which text an indexed row came from. Node/resource rows key off the backing node's id;
    document rows key off the document id."""

    NODE = "node"
    RESOURCE = "resource"
    DOCUMENT = "document"


class SearchScope(StrEnum):
    """The domain tab a search hit belongs to (EP-2026-012 ST-12): stored on each indexed row so
    scope filtering is a cheap SQL predicate and pagination stays stable. `ALL` is the absence of
    a filter, never a stored scope."""

    ALL = "all"
    WIKI = "wiki"
    TASKS = "tasks"
    RESEARCH = "research"
    REPOSITORIES = "repositories"
    GRAPH = "graph"


class SearchHit(BaseModel):
    """One raw FTS5 match, before resolving it back to its canonical entity."""

    entity_type: SearchEntityType
    entity_id: str
    snippet: str
    score: float
    scope: SearchScope = SearchScope.ALL


class SearchRequest(BaseModel):
    """One search engine invocation: workspace, query, scope filter, and page window.

    `include_archived` is evaluated inside the engine (a join against the canonical nodes/documents
    tables), so `offset`/`total`/`has_more` describe the exact set the caller sees -- archived and
    duplicate (node+resource) rows are filtered before windowing (S12-F01).
    """

    workspace_id: WorkspaceId
    query: str
    scope: SearchScope = SearchScope.ALL
    limit: int = 20
    offset: int = 0
    include_archived: bool = False


class SearchPage(BaseModel):
    """The engine's deterministic, page-windowed result: deduplicated, archived-filtered hits plus
    a stable total/has_more that match the caller-visible entity set."""

    hits: tuple[SearchHit, ...]
    total: int
    has_more: bool


class SearchEngine(Protocol):
    """The read port `SearchService` depends on (EP-2026-012 ST-12): a replaceable full-text
    engine. `SqliteBm25SearchEngine` is today's implementation; a later hybrid/semantic engine is
    a new implementation of this same port, leaving REST/MCP/UI callers unchanged."""

    def search(self, request: SearchRequest) -> SearchPage: ...


_RESEARCH_RESOURCE_KINDS = frozenset({ResourceKind.PAPER, ResourceKind.ARTICLE})
_REPOSITORY_RESOURCE_KINDS = frozenset({ResourceKind.GITHUB_REPOSITORY})


def search_scope_for_resource_kind(kind: ResourceKind) -> SearchScope:
    """The search scope a resource (and its backing node's indexed text) belongs to (ST-12):
    papers/articles → `research`, GitHub repositories → `repositories`, everything else (e.g. a
    dataset or book) → `graph` (it is a generic graph object, not a Research/Repositories tab
    projection)."""
    if kind in _RESEARCH_RESOURCE_KINDS:
        return SearchScope.RESEARCH
    if kind in _REPOSITORY_RESOURCE_KINDS:
        return SearchScope.REPOSITORIES
    return SearchScope.GRAPH


class SearchResult(BaseModel):
    """A search hit resolved back to canonical data: the projection a caller renders."""

    node: Node | None
    resource: Resource | None
    document: Document | None
    snippet: str
    score: float
    scope: SearchScope
    entity_type: SearchEntityType
    source: str | None
    goto: str | None


class SearchResultPage(BaseModel):
    """A `SearchService` page: resolved results plus the stable pagination contract the engine
    computed (S12-F01). `offset` is the caller's page start; `has_more`/`total` describe the
    caller-visible (deduplicated, archived-filtered) entity set."""

    results: tuple[SearchResult, ...]
    total: int
    has_more: bool
    offset: int


def compile_fts5_query(raw_query: str) -> str | None:
    """Compile free-text user input into a safe FTS5 `MATCH` query.

    Plain input must never be interpreted as FTS5 query syntax: a title containing `AND`,
    `NOT`, `(`, `:`, or `*` would otherwise change the query's boolean structure or raise a
    syntax error instead of being searched for literally. Each whitespace-separated token is
    wrapped as a quoted phrase (with embedded quotes doubled, FTS5's escape convention) and
    given a trailing `*` for prefix matching, then joined with an explicit `AND` so every term
    must match. Returns `None` for empty/whitespace-only input, which callers treat as no
    results rather than an unbounded "match everything" query.
    """
    tokens = raw_query.strip().split()
    if not tokens:
        return None
    terms = [f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens]
    return " AND ".join(terms)
