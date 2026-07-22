"""SQLite implementation of `ResearchUnitOfWork` using explicit `BEGIN`/`SAVEPOINT`.

The HTTP API's single shared connection already serializes all request handling behind one
`anyio.Lock` (see `personal_graph_os.api.app`), so this adapter does not need to defend against
concurrent writers on the same connection — only against a partially applied multi-table write
when a later step in the same operation fails.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType

from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteContextPackRepository,
    SqliteDiscoveryRunRepository,
    SqliteEdgeRepository,
    SqliteNodeRepository,
    SqliteResourceRepository,
)


class SqliteResearchUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.nodes = SqliteNodeRepository(connection)
        self.resources = SqliteResourceRepository(connection)
        self.edges = SqliteEdgeRepository(connection)
        self.discovery_runs = SqliteDiscoveryRunRepository(connection)
        self.activity_events = SqliteActivityEventRepository(connection)
        self.context_packs = SqliteContextPackRepository(connection)
        self._savepoint_count = 0

    def __enter__(self) -> SqliteResearchUnitOfWork:
        self._connection.execute("BEGIN")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()

    @contextmanager
    def savepoint(self) -> Generator[None]:
        self._savepoint_count += 1
        name = f"research_uow_sp_{self._savepoint_count}"
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
