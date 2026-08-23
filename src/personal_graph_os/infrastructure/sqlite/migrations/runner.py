"""Apply versioned `.sql` migration files from `versions/` in filename order.

Each migration runs at most once per database, tracked by filename in
`schema_migrations`. Migrations are plain SQL so they can be inspected and replayed
without importing application code.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from importlib.resources.abc import Traversable

_VERSIONS_PACKAGE = "personal_graph_os.infrastructure.sqlite.migrations.versions"

# Historical names under which a migration file was previously shipped. A database that
# applied it before a rename records the old filename; treating that record as satisfying
# the current name prevents replaying DDL against tables that already exist.
_RENAMED_MIGRATIONS = {
    "0020_resource_content.sql": "0016_resource_content.sql",
}


def _sorted_migration_files() -> list[Traversable]:
    versions_directory = resources.files(_VERSIONS_PACKAGE)
    return sorted(
        (entry for entry in versions_directory.iterdir() if entry.name.endswith(".sql")),
        key=lambda entry: entry.name,
    )


def applied_migration_names(connection: sqlite3.Connection) -> set[str]:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    rows = connection.execute("SELECT name FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_migration_script(connection: sqlite3.Connection, name: str, sql_script: str) -> None:
    """Apply one migration's SQL and its `schema_migrations` marker as a single atomic unit.

    Delegates statement splitting to SQLite itself via `executescript()` (block comments,
    quoted identifiers, and trigger bodies are real SQL grammar that only SQLite's own
    parser handles correctly) rather than a hand-written splitter. The script starts with
    an explicit `BEGIN` and deliberately has no matching `COMMIT`, so `executescript()`
    returns with the transaction still open; the `schema_migrations` marker is then
    inserted (parameterized, avoiding any literal-escaping concern) inside that same
    transaction before one explicit `commit()`. On any failure, `rollback()` undoes the
    migration's DDL and the marker together, so a retry after fixing the script starts
    from a clean, unmigrated state rather than a partially applied one.
    """
    try:
        connection.executescript(f"BEGIN;\n{sql_script}")
        connection.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def run_migrations(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Apply every not-yet-applied migration in order. Returns the names just applied."""
    already_applied = applied_migration_names(connection)
    newly_applied: list[str] = []

    for migration_file in _sorted_migration_files():
        historical_name = _RENAMED_MIGRATIONS.get(migration_file.name)
        if migration_file.name in already_applied or (
            historical_name is not None and historical_name in already_applied
        ):
            continue
        sql_script = migration_file.read_text(encoding="utf-8")
        apply_migration_script(connection, migration_file.name, sql_script)
        newly_applied.append(migration_file.name)

    return tuple(newly_applied)
