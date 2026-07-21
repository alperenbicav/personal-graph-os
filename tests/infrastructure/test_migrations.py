from __future__ import annotations

import sqlite3

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
}


def test_run_migrations_creates_every_domain_table() -> None:
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    table_names = {row[0] for row in rows}
    assert _EXPECTED_TABLES.issubset(table_names)


def test_run_migrations_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    first_pass = run_migrations(connection)
    second_pass = run_migrations(connection)
    assert first_pass == ("0001_initial_schema.sql",)
    assert second_pass == ()
    assert applied_migration_names(connection) == {"0001_initial_schema.sql"}


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
