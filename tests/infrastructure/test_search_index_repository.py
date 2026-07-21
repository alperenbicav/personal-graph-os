from __future__ import annotations

import sqlite3

from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.search import SearchEntityType
from personal_graph_os.infrastructure.sqlite.repositories import SqliteSearchIndexRepository


def test_index_document_then_search_finds_it_by_prefix(
    sqlite_connection: sqlite3.Connection,
) -> None:
    repository = SqliteSearchIndexRepository(sqlite_connection)
    workspace_id = WorkspaceId("w1")

    repository.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="Attention Is All You Need",
    )

    hits = repository.search(workspace_id, "atten")

    assert [hit.entity_id for hit in hits] == ["n1"]
    assert hits[0].entity_type is SearchEntityType.NODE


def test_index_document_replaces_the_prior_text_for_the_same_entity(
    sqlite_connection: sqlite3.Connection,
) -> None:
    repository = SqliteSearchIndexRepository(sqlite_connection)
    workspace_id = WorkspaceId("w1")

    repository.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="Old text",
    )
    repository.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="New text",
    )

    assert repository.search(workspace_id, "old") == ()
    assert [hit.entity_id for hit in repository.search(workspace_id, "new")] == ["n1"]


def test_remove_document_drops_it_from_search(sqlite_connection: sqlite3.Connection) -> None:
    repository = SqliteSearchIndexRepository(sqlite_connection)
    workspace_id = WorkspaceId("w1")
    repository.index_document(
        workspace_id=workspace_id,
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="Removable",
    )

    repository.remove_document(entity_type=SearchEntityType.NODE, entity_id="n1")

    assert repository.search(workspace_id, "removable") == ()


def test_search_is_scoped_to_the_given_workspace(sqlite_connection: sqlite3.Connection) -> None:
    repository = SqliteSearchIndexRepository(sqlite_connection)
    repository.index_document(
        workspace_id=WorkspaceId("w1"),
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="Shared keyword",
    )
    repository.index_document(
        workspace_id=WorkspaceId("w2"),
        entity_type=SearchEntityType.NODE,
        entity_id="n2",
        text="Shared keyword",
    )

    hits = repository.search(WorkspaceId("w1"), "shared")

    assert [hit.entity_id for hit in hits] == ["n1"]


def test_search_returns_no_hits_for_blank_query(sqlite_connection: sqlite3.Connection) -> None:
    repository = SqliteSearchIndexRepository(sqlite_connection)
    repository.index_document(
        workspace_id=WorkspaceId("w1"),
        entity_type=SearchEntityType.NODE,
        entity_id="n1",
        text="Text",
    )

    assert repository.search(WorkspaceId("w1"), "   ") == ()


def test_match_query_uses_the_fts5_virtual_table_index_not_a_full_table_scan(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Acceptance: FTS query plans/index usage. `SCAN ... VIRTUAL TABLE INDEX` proves SQLite
    resolved the `MATCH` through FTS5's own index rather than evaluating every row."""
    plan_rows = sqlite_connection.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT entity_type, entity_id FROM search_documents "
        "WHERE search_documents MATCH ? AND workspace_id = ?",
        ('"hello"*', "w1"),
    ).fetchall()

    plan_detail = " | ".join(row["detail"] for row in plan_rows)
    assert "VIRTUAL TABLE INDEX" in plan_detail
