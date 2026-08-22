"""Cross-connection serialization guarantees behind the Telegram poller's own connection.

The API request connection is serialized by an in-process lock; the Telegram poller writes
through a second connection. WAL journaling plus `busy_timeout` must make those writers
wait for each other at the database level instead of failing with "database is locked", and
a reader on the other connection must keep seeing the last committed snapshot.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from personal_graph_os.infrastructure.sqlite.connection import open_connection
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from tests.conftest import run_migrations


def _seed_workspace(connection: sqlite3.Connection) -> None:
    with SqliteResearchUnitOfWork(connection):
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES ('ws-1', 'P', 't0')"
        )
        connection.execute(
            "INSERT INTO node_types (id, workspace_id, name) VALUES ('nt-1', 'ws-1', 'Note')"
        )


def test_a_second_connections_write_waits_for_an_open_transaction_instead_of_failing(
    tmp_path,
) -> None:
    connection_a = open_connection(tmp_path / "graph.db", check_same_thread=False)
    connection_b = open_connection(tmp_path / "graph.db", check_same_thread=False)
    run_migrations(connection_a)
    _seed_workspace(connection_b)

    first_committed = threading.Event()

    def _hold_and_commit() -> None:
        with SqliteResearchUnitOfWork(connection_a):
            connection_a.execute(
                "INSERT INTO nodes (id, workspace_id, node_type_id, title, created_at, updated_at)"
                " VALUES ('node-a', 'ws-1', 'nt-1', 'from-a', 't0', 't0')"
            )
            time.sleep(0.2)
        first_committed.set()

    worker = threading.Thread(target=_hold_and_commit)
    worker.start()
    try:
        # While A's transaction is open, B's write must block on busy_timeout, not raise.
        with SqliteResearchUnitOfWork(connection_b):
            connection_b.execute(
                "UPDATE workspaces SET name = 'P2' WHERE id = 'ws-1'"
            )
        assert first_committed.wait(timeout=5.0)
    finally:
        worker.join(timeout=5.0)

    node_count = connection_b.execute(
        "SELECT COUNT(*) FROM nodes WHERE workspace_id = 'ws-1'"
    ).fetchone()[0]
    assert node_count == 1


def test_readers_on_another_connection_do_not_block_behind_a_writer(tmp_path) -> None:
    connection_writer = open_connection(tmp_path / "graph.db", check_same_thread=False)
    connection_reader = open_connection(tmp_path / "graph.db", check_same_thread=False)
    run_migrations(connection_writer)
    _seed_workspace(connection_reader)

    with SqliteResearchUnitOfWork(connection_writer):
        connection_writer.execute(
            "INSERT INTO nodes (id, workspace_id, node_type_id, title, created_at, updated_at)"
            " VALUES ('node-a', 'ws-1', 'nt-1', 'uncommitted', 't0', 't0')"
        )
        # A reader on the second connection keeps seeing the last committed snapshot.
        workspace_count = connection_reader.execute(
            "SELECT COUNT(*) FROM workspaces"
        ).fetchone()[0]
        assert workspace_count == 1

    connection_reader.close()
    connection_writer.close()


def test_open_connection_sets_busy_timeout_for_cross_connection_writers(tmp_path) -> None:
    connection = open_connection(tmp_path / "graph.db")
    timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert isinstance(timeout, int)
    assert timeout > 0
    assert isinstance(connection, sqlite3.Connection)
