"""SQLite connection factory: WAL mode, foreign keys, and row access by column name."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DEFAULT_BUSY_TIMEOUT_MS = 5_000


def open_connection(
    database_path: Path | str,
    *,
    check_same_thread: bool = True,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open a SQLite connection configured for local-first, single-process use.

    WAL mode allows concurrent readers alongside a writer; foreign keys are enforced so
    referential invariants (e.g. an edge's node ids) are caught by the database, not only
    by application code. `busy_timeout_ms` makes a second writer (e.g. the Telegram
    poller's own connection) wait for the current writer instead of failing immediately
    with "database is locked".

    `check_same_thread=False` is for a long-lived server (the HTTP API) whose single
    connection is reused across requests that a thread pool may dispatch to different
    worker threads; callers that share the connection across threads must otherwise
    serialize access themselves (see `personal_graph_os.api.app`, which does).
    """
    connection = sqlite3.connect(database_path, check_same_thread=check_same_thread)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return connection
