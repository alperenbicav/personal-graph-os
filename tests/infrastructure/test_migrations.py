from __future__ import annotations

import sqlite3
from importlib import resources

import pytest

from personal_graph_os.infrastructure.sqlite.migrations.runner import (
    applied_migration_names,
    apply_migration_script,
    run_migrations,
)

_EXPECTED_TABLES = {
    "workspaces",
    "node_types",
    "field_definitions",
    "status_definitions",
    "edge_types",
    "nodes",
    "edges",
    "canvases",
    "canvas_placements",
    "saved_views",
    "context_packs",
    "attachments",
    "file_references",
    "activity_events",
    "discovery_runs",
    "resources",
    "workspace_research_settings",
    "search_documents",
    "pending_file_operations",
    "idempotency_receipts",
    "collections",
    "tags",
    "documents",
    "document_tags",
    "document_versions",
    "document_links",
    "ingestion_jobs",
    "work_items",
}


def test_run_migrations_creates_every_domain_table() -> None:
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    table_names = {row[0] for row in rows}
    assert _EXPECTED_TABLES.issubset(table_names)


_ALL_MIGRATION_NAMES = (
    "0001_initial_schema.sql",
    "0002_research_library.sql",
    "0003_resource_progress.sql",
    "0004_pending_file_operations.sql",
    "0005_activity_event_request_id.sql",
    "0006_idempotency_receipts.sql",
    "0007_activity_event_reversal.sql",
    "0008_agentic_os_foundation.sql",
    "0009_document_source_reference.sql",
    "0010_enrichment.sql",
    "0011_edge_uniqueness.sql",
)


def test_run_migrations_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    first_pass = run_migrations(connection)
    second_pass = run_migrations(connection)
    assert first_pass == _ALL_MIGRATION_NAMES
    assert second_pass == ()
    assert applied_migration_names(connection) == set(_ALL_MIGRATION_NAMES)


def test_upgrading_an_existing_0001_database_preserves_ids_and_data() -> None:
    """0001 -> 0002 must not lose or re-key any pre-existing row, including in `resources`,
    which 0002 recreates to relax its uniqueness constraint."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    applied_migration_names(connection)
    zero_one_sql = (
        resources.files("personal_graph_os.infrastructure.sqlite.migrations.versions")
        / "0001_initial_schema.sql"
    ).read_text(encoding="utf-8")
    apply_migration_script(connection, "0001_initial_schema.sql", zero_one_sql)

    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Resource')"
    )
    connection.execute(
        "INSERT INTO nodes "
        "(id, workspace_id, node_type_id, title, created_at, updated_at) "
        "VALUES ('node-1', 'ws-1', 'nt-1', 'A paper', 't0', 't0')"
    )
    connection.execute(
        "INSERT INTO resources "
        "(id, workspace_id, node_id, kind, canonical_identifier, last_activity_at) "
        "VALUES ('res-1', 'ws-1', 'node-1', 'paper', 'arxiv:1', 't0')"
    )
    connection.commit()

    newly_applied = run_migrations(connection)
    assert newly_applied == (
        "0002_research_library.sql",
        "0003_resource_progress.sql",
        "0004_pending_file_operations.sql",
        "0005_activity_event_request_id.sql",
        "0006_idempotency_receipts.sql",
        "0007_activity_event_reversal.sql",
        "0008_agentic_os_foundation.sql",
        "0009_document_source_reference.sql",
        "0010_enrichment.sql",
        "0011_edge_uniqueness.sql",
    )

    resource_row = connection.execute("SELECT * FROM resources WHERE id = 'res-1'").fetchone()
    assert resource_row["node_id"] == "node-1"
    assert resource_row["canonical_identifier"] == "arxiv:1"
    assert resource_row["next_action_dismissed"] == 0
    node_type_row = connection.execute("SELECT * FROM node_types WHERE id = 'nt-1'").fetchone()
    assert node_type_row["system_key"] is None
    assert resource_row["progress_percent"] is None


def test_0003_adds_a_nullable_progress_percent_column(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """ST04-F01: `resources.progress_percent` round-trips through a raw update, independent
    of the domain layer, proving the column itself is correctly additive/nullable."""
    connection = sqlite_connection
    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Resource')"
    )
    connection.execute(
        "INSERT INTO nodes "
        "(id, workspace_id, node_type_id, title, created_at, updated_at) "
        "VALUES ('node-1', 'ws-1', 'nt-1', 'A paper', 't0', 't0')"
    )
    connection.execute(
        "INSERT INTO resources "
        "(id, workspace_id, node_id, kind, canonical_identifier, last_activity_at) "
        "VALUES ('res-1', 'ws-1', 'node-1', 'paper', 'arxiv:1', 't0')"
    )
    connection.commit()

    row = connection.execute("SELECT progress_percent FROM resources WHERE id = 'res-1'").fetchone()
    assert row["progress_percent"] is None

    connection.execute("UPDATE resources SET progress_percent = 42 WHERE id = 'res-1'")
    connection.commit()
    row = connection.execute("SELECT progress_percent FROM resources WHERE id = 'res-1'").fetchone()
    assert row["progress_percent"] == 42


def test_0007_reverses_event_id_is_nullable_and_at_most_one_per_reversed_event(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """ST-07.1: existing events keep `reverses_event_id = NULL`, and the unique partial index
    rejects a second compensating event pointing at the same reversed event."""
    connection = sqlite_connection
    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.executemany(
        "INSERT INTO activity_events "
        "(id, workspace_id, actor_kind, actor_name, source, entity_type, entity_id, action, "
        " occurred_at) VALUES (?, 'ws-1', 'human', 'me', 'rest', 'node', 'node-1', 'updated', ?)",
        [("evt-1", "t0"), ("evt-2", "t1"), ("evt-3", "t2")],
    )
    connection.commit()

    row = connection.execute(
        "SELECT reverses_event_id FROM activity_events WHERE id = 'evt-1'"
    ).fetchone()
    assert row["reverses_event_id"] is None

    connection.execute("UPDATE activity_events SET reverses_event_id = 'evt-1' WHERE id = 'evt-2'")
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE activity_events SET reverses_event_id = 'evt-1' WHERE id = 'evt-3'"
        )


def test_apply_migration_script_is_atomic_and_a_corrected_retry_succeeds() -> None:
    """Regression for ST-01 review finding: a multi-statement migration where a later
    statement fails must leave no partial schema and no `schema_migrations` marker, and a
    corrected retry of the same migration name must then succeed cleanly."""
    connection = sqlite3.connect(":memory:")
    applied_migration_names(connection)  # ensures schema_migrations exists

    failing_script = """
    CREATE TABLE partial_a (id TEXT);
    CREATE TABLE partial_b (id TEXT);
    INSERT INTO no_such_table VALUES (1);
    """
    with pytest.raises(sqlite3.OperationalError):
        apply_migration_script(connection, "999_broken.sql", failing_script)

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "partial_a" not in table_names
    assert "partial_b" not in table_names
    assert applied_migration_names(connection) == set()

    corrected_script = """
    CREATE TABLE partial_a (id TEXT);
    CREATE TABLE partial_b (id TEXT);
    """
    apply_migration_script(connection, "999_broken.sql", corrected_script)

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"partial_a", "partial_b"}.issubset(table_names)
    assert applied_migration_names(connection) == {"999_broken.sql"}


def test_apply_migration_script_handles_block_comment_with_a_semicolon_inside() -> None:
    """Regression for ST-01 review finding: statement splitting must be SQLite's own, not
    a hand-written parser that misreads a `;` inside a `/* ... */` block comment."""
    connection = sqlite3.connect(":memory:")
    applied_migration_names(connection)

    script = """
    /* a block comment; with a semicolon inside */
    CREATE TABLE with_block_comment (id TEXT);
    """
    apply_migration_script(connection, "999_block_comment.sql", script)

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "with_block_comment" in table_names


def test_apply_migration_script_handles_semicolon_in_quoted_identifier() -> None:
    """Regression for ST-01 review finding: a `;` inside a double-quoted identifier is
    part of the identifier, not a statement boundary."""
    connection = sqlite3.connect(":memory:")
    applied_migration_names(connection)

    script = 'CREATE TABLE "weird;name" (id TEXT);'
    apply_migration_script(connection, "999_quoted_identifier.sql", script)

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "weird;name" in table_names


def test_apply_migration_script_handles_trigger_body_with_semicolons() -> None:
    """Regression for ST-01 review finding: a `CREATE TRIGGER ... BEGIN ... END;` body
    contains its own semicolons that are not top-level statement boundaries."""
    connection = sqlite3.connect(":memory:")
    applied_migration_names(connection)

    script = """
    CREATE TABLE source_rows (id TEXT, value TEXT);
    CREATE TABLE mirrored_rows (id TEXT, value TEXT);
    CREATE TRIGGER mirror_insert AFTER INSERT ON source_rows BEGIN
        INSERT INTO mirrored_rows (id, value) VALUES (NEW.id, NEW.value);
    END;
    """
    apply_migration_script(connection, "999_trigger.sql", script)

    connection.execute("INSERT INTO source_rows (id, value) VALUES ('1', 'hello')")
    mirrored = connection.execute("SELECT id, value FROM mirrored_rows").fetchall()
    assert mirrored == [("1", "hello")]
    assert applied_migration_names(connection) == {"999_trigger.sql"}


def test_upgrading_an_existing_0007_database_preserves_resources_and_nodes() -> None:
    """ST-01 (EP-2026-012): 0008 is purely additive -- a pre-existing resource/node must still
    round-trip unchanged after the new document/ingestion/work-item tables are added."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    applied_migration_names(connection)
    for name in _ALL_MIGRATION_NAMES[:7]:
        sql_script = (
            resources.files("personal_graph_os.infrastructure.sqlite.migrations.versions") / name
        ).read_text(encoding="utf-8")
        apply_migration_script(connection, name, sql_script)

    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Resource')"
    )
    connection.execute(
        "INSERT INTO nodes "
        "(id, workspace_id, node_type_id, title, created_at, updated_at) "
        "VALUES ('node-1', 'ws-1', 'nt-1', 'A paper', 't0', 't0')"
    )
    connection.execute(
        "INSERT INTO resources "
        "(id, workspace_id, node_id, kind, canonical_identifier, last_activity_at) "
        "VALUES ('res-1', 'ws-1', 'node-1', 'paper', 'arxiv:1', 't0')"
    )
    connection.commit()

    newly_applied = run_migrations(connection)
    assert newly_applied == (
        "0008_agentic_os_foundation.sql",
        "0009_document_source_reference.sql",
        "0010_enrichment.sql",
        "0011_edge_uniqueness.sql",
    )

    resource_row = connection.execute("SELECT * FROM resources WHERE id = 'res-1'").fetchone()
    assert resource_row["node_id"] == "node-1"
    assert resource_row["canonical_identifier"] == "arxiv:1"


def test_upgrading_a_pre_0011_database_with_duplicate_edges_keeps_one_canonical_row() -> None:
    """ST-04 (EP-2026-012), review finding S4-R05: a database created before 0011 could already
    contain duplicate (workspace_id, edge_type_id, source_node_id, target_node_id) rows, since no
    prior constraint prevented it. `CREATE UNIQUE INDEX` alone fails with `IntegrityError` against
    such data; 0011 must reconcile duplicates first so the upgrade completes and exactly one
    canonical edge survives per tuple."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    applied_migration_names(connection)
    for name in _ALL_MIGRATION_NAMES[:10]:
        sql_script = (
            resources.files("personal_graph_os.infrastructure.sqlite.migrations.versions") / name
        ).read_text(encoding="utf-8")
        apply_migration_script(connection, name, sql_script)

    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Resource')"
    )
    connection.execute(
        "INSERT INTO edge_types (id, workspace_id, name) VALUES ('et-1', 'ws-1', 'relates_to')"
    )
    for node_id in ("node-1", "node-2"):
        connection.execute(
            "INSERT INTO nodes "
            "(id, workspace_id, node_type_id, title, created_at, updated_at) "
            f"VALUES ('{node_id}', 'ws-1', 'nt-1', 'A node', 't0', 't0')"
        )
    # Two edges with the identical (workspace_id, edge_type_id, source_node_id, target_node_id)
    # tuple -- exactly the state no prior migration/constraint prevented -- with distinct
    # `created_at` so the earlier one is the deterministic survivor.
    connection.execute(
        "INSERT INTO edges "
        "(id, workspace_id, edge_type_id, source_node_id, target_node_id, created_at) "
        "VALUES ('edge-older', 'ws-1', 'et-1', 'node-1', 'node-2', 't0')"
    )
    connection.execute(
        "INSERT INTO edges "
        "(id, workspace_id, edge_type_id, source_node_id, target_node_id, created_at) "
        "VALUES ('edge-newer', 'ws-1', 'et-1', 'node-1', 'node-2', 't1')"
    )
    connection.commit()

    newly_applied = run_migrations(connection)
    assert newly_applied == ("0011_edge_uniqueness.sql",)

    rows = connection.execute(
        "SELECT id FROM edges WHERE workspace_id = 'ws-1' AND edge_type_id = 'et-1' "
        "AND source_node_id = 'node-1' AND target_node_id = 'node-2'"
    ).fetchall()
    assert [row["id"] for row in rows] == ["edge-older"]


def test_work_item_node_id_is_unique_and_ingestion_job_source_identity_is_unique(
    sqlite_connection: sqlite3.Connection,
) -> None:
    connection = sqlite_connection
    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Task')"
    )
    connection.execute(
        "INSERT INTO nodes "
        "(id, workspace_id, node_type_id, title, created_at, updated_at) "
        "VALUES ('node-1', 'ws-1', 'nt-1', 'Ship it', 't0', 't0')"
    )
    connection.execute(
        "INSERT INTO work_items "
        "(id, workspace_id, node_id, kind, work_type, source, created_at, updated_at) "
        "VALUES ('wi-1', 'ws-1', 'node-1', 'task', 'fix', 'manual', 't0', 't0')"
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO work_items "
            "(id, workspace_id, node_id, kind, work_type, source, created_at, updated_at) "
            "VALUES ('wi-2', 'ws-1', 'node-1', 'task', 'fix', 'manual', 't0', 't0')"
        )

    connection.execute(
        "INSERT INTO ingestion_jobs "
        "(id, workspace_id, source, source_identifier, created_at, updated_at) "
        "VALUES ('job-1', 'ws-1', 'telegram', 'update:1', 't0', 't0')"
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO ingestion_jobs "
            "(id, workspace_id, source, source_identifier, created_at, updated_at) "
            "VALUES ('job-2', 'ws-1', 'telegram', 'update:1', 't0', 't0')"
        )


def test_resources_uniqueness_is_workspace_and_identifier_independent_of_kind() -> None:
    """Decision #5: canonical identity is unique per workspace regardless of `kind`."""
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)
    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'Personal', 't0')"
    )
    connection.execute(
        "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Resource')"
    )
    for node_id in ("node-1", "node-2"):
        connection.execute(
            "INSERT INTO nodes "
            "(id, workspace_id, node_type_id, title, created_at, updated_at) "
            f"VALUES ('{node_id}', 'ws-1', 'nt-1', 'x', 't0', 't0')"
        )
    connection.execute(
        "INSERT INTO resources "
        "(id, workspace_id, node_id, kind, canonical_identifier, last_activity_at) "
        "VALUES ('res-1', 'ws-1', 'node-1', 'paper', 'shared-id', 't0')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO resources "
            "(id, workspace_id, node_id, kind, canonical_identifier, last_activity_at) "
            "VALUES ('res-2', 'ws-1', 'node-2', 'github_repository', 'shared-id', 't0')"
        )
