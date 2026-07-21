"""Full-text search over node/resource text: safe query compilation and result shape.

Indexing and the FTS5 storage itself are an infrastructure concern (`SqliteSearchIndexRepository`);
this module owns the one thing that must stay provider-agnostic and independently testable: turning
arbitrary user input into a query FTS5 can execute safely, plus the typed hit/result shapes callers
work with.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.resource import Resource


def build_node_search_text(node: Node) -> str:
    """The text `NodeService` indexes for a node's own title/body."""
    return f"{node.title}\n{node.body}"


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
    """Which text an indexed row came from. Both kinds key off the backing node's id."""

    NODE = "node"
    RESOURCE = "resource"


class SearchHit(BaseModel):
    """One raw FTS5 match, before resolving it back to its canonical `Node`/`Resource`."""

    entity_type: SearchEntityType
    entity_id: str
    snippet: str
    rank: float


class SearchResult(BaseModel):
    """A search hit resolved back to canonical data: the projection a caller renders."""

    node: Node
    resource: Resource | None
    snippet: str


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
