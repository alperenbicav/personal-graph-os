from __future__ import annotations

import logging
import sqlite3

import pytest

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.telegram_adapters import (
    TelegramChat,
    TelegramMessage,
    TelegramRateLimitedError,
    TelegramUpdate,
    TelegramUser,
)
from personal_graph_os.application.telegram_poller import TelegramPoller
from personal_graph_os.application.telegram_service import TelegramService
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.telegram.fake_client import FakeTelegramClient


def _update(update_id: int) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=update_id,
            date=1720000000,
            chat=TelegramChat(id=1),
            from_=TelegramUser(id=99, is_bot=False),
            text="https://example.com/article",
        ),
    )


def _service(
    sqlite_connection: sqlite3.Connection,
    fake_client: FakeTelegramClient,
    *,
    workspace_id=None,
) -> tuple[TelegramService, object]:
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    if workspace_id is None:
        workspace = ensure_semantic_schema(new_workspace("Personal"))
        workspace_repository.save(workspace)
        resolved_workspace_id = workspace.id
    else:
        resolved_workspace_id = workspace_id
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    resource_service = ResourceService(
        workspace_repository, SqliteResourceRepository(sqlite_connection), unit_of_work_factory
    )
    capture_service = CaptureService(
        workspace_repository,
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteIdempotencyReceiptRepository(sqlite_connection),
        resource_service,
        unit_of_work_factory,
    )
    orchestrator = CapturePlanningOrchestrator(capture_service, None, unit_of_work_factory)
    return (
        TelegramService(fake_client, orchestrator, resolved_workspace_id, unit_of_work_factory),
        resolved_workspace_id,
    )


def test_poll_once_handles_the_resumed_batch_and_advances_the_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    fake_client = FakeTelegramClient(updates=(_update(1), _update(2)))
    service, workspace_id = _service(sqlite_connection, fake_client)
    poller = TelegramPoller(fake_client, service)

    handled = poller.poll_once()

    assert handled == 2
    assert service.current_offset() == 3
    assert len(fake_client.outbox) >= 4


def test_poll_once_resumes_from_the_stored_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    fake_client = FakeTelegramClient(updates=(_update(1), _update(2)))
    service, workspace_id = _service(sqlite_connection, fake_client)
    poller = TelegramPoller(fake_client, service)
    poller.poll_once()

    requested_offsets: list[int | None] = []

    class _RecordingClient(FakeTelegramClient):
        def get_updates(self, *, offset, timeout_seconds):
            requested_offsets.append(offset)
            return ()

    recording_client = _RecordingClient(updates=())
    restarted_service, _restarted_workspace = _service(
        sqlite_connection, recording_client, workspace_id=workspace_id
    )
    restarted_poller = TelegramPoller(recording_client, restarted_service)

    restarted_poller.poll_once()

    assert requested_offsets == [3]


def test_run_stops_when_should_stop_returns_true(
    sqlite_connection: sqlite3.Connection,
) -> None:
    fake_client = FakeTelegramClient(updates=())
    service, _workspace_id = _service(sqlite_connection, fake_client)
    poller = TelegramPoller(fake_client, service, idle_sleep_seconds=0.0, backoff_seconds=0.0)
    iterations = 0

    def should_stop() -> bool:
        nonlocal iterations
        iterations += 1
        return iterations >= 2

    poller.run(should_stop)

    assert iterations == 2


def test_run_backs_off_and_recovers_after_a_rate_limit(
    sqlite_connection: sqlite3.Connection,
) -> None:
    calls = 0

    class _RateLimitedClient(FakeTelegramClient):
        def get_updates(self, *, offset, timeout_seconds):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TelegramRateLimitedError("rate limited")
            return ()

    fake_client = _RateLimitedClient(updates=())
    service, _workspace_id = _service(sqlite_connection, fake_client)
    poller = TelegramPoller(fake_client, service, idle_sleep_seconds=0.0, backoff_seconds=0.0)
    iterations = 0

    def should_stop() -> bool:
        nonlocal iterations
        iterations += 1
        return iterations >= 3

    poller.run(should_stop)

    assert calls == 2


def test_run_survives_an_unexpected_error_and_logs_it(
    sqlite_connection: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-Telegram failure (e.g. a database hiccup) must not silently kill polling for
    the rest of the process lifetime: the loop logs it with its traceback and continues."""
    calls = 0

    class _BrokenThenWorkingClient(FakeTelegramClient):
        def get_updates(self, *, offset, timeout_seconds):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated unexpected failure")
            return ()

    fake_client = _BrokenThenWorkingClient(updates=())
    service, _workspace_id = _service(sqlite_connection, fake_client)
    poller = TelegramPoller(fake_client, service, idle_sleep_seconds=0.0, backoff_seconds=0.0)
    iterations = 0

    def should_stop() -> bool:
        nonlocal iterations
        iterations += 1
        return iterations >= 3

    with caplog.at_level(logging.ERROR, logger="personal_graph_os.application.telegram_poller"):
        poller.run(should_stop)

    assert calls == 2  # polling continued past the failure
    failure_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(failure_records) == 1
    assert "Unexpected telegram poller failure" in failure_records[0].getMessage()
    assert failure_records[0].exc_info is not None
