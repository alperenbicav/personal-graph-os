from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from personal_graph_os.infrastructure.sqlite.migrations.runner import run_migrations


@pytest.fixture
def sqlite_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    run_migrations(connection)
    try:
        yield connection
    finally:
        connection.close()
