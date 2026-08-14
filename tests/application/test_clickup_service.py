from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.clickup_adapters import (
    ClickUpNotConfiguredError,
    ClickUpTask,
    ClickUpTaskNotFoundError,
)
from personal_graph_os.application.clickup_service import CLICKUP_CHANNEL_NAME, ClickupService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.domain.capture import (
    CaptureIdempotencyConflictError,
    CaptureIntent,
    CaptureOperationKind,
)
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.infrastructure.clickup.fake_client import FakeClickUpClient
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteChannelSyncStateRepository,
    SqliteDocumentRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.work_planning.fake_provider import FakeWorkPlanningProvider


def _task(**overrides: object) -> ClickUpTask:
    fields: dict[str, object] = {
        "id": "task-1",
        "name": "Implement signup",
        "description": "Add OAuth login",
        "url": "https://app.clickup.com/t/task-1",
        "date_updated": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return ClickUpTask.model_validate(fields)


def _clickup_service(
    sqlite_connection: sqlite3.Connection,
    *,
    tasks: dict[str, ClickUpTask] | None = None,
    work_planning_service: WorkPlanningService | None = None,
    client: object | None = ...,
) -> tuple[ClickupService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
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
    orchestrator = CapturePlanningOrchestrator(
        capture_service, work_planning_service, unit_of_work_factory
    )
    resolved_client = FakeClickUpClient(tasks or {}) if client is ... else client
    clickup_service = ClickupService(
        resolved_client,  # type: ignore[arg-type]
        orchestrator,
        workspace_repository,
        unit_of_work_factory,
    )
    return clickup_service, workspace.id


def _work_planning_service(
    sqlite_connection: sqlite3.Connection,
) -> WorkPlanningService:
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    work_item_service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        unit_of_work_factory,
    )
    return WorkPlanningService(work_item_service, FakeWorkPlanningProvider(), unit_of_work_factory)


def test_save_raw_import_creates_a_raw_document_with_the_task_as_its_identity(
    sqlite_connection: sqlite3.Connection,
) -> None:
    task = _task()
    clickup_service, workspace_id = _clickup_service(sqlite_connection, tasks={task.id: task})

    outcome, plan_outcome = clickup_service.import_item(
        workspace_id, task_id=task.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )

    assert outcome.document_id is not None
    assert plan_outcome is None
    assert outcome.needs_clarification is False
    assert outcome.clarification_reason is None

    document = SqliteDocumentRepository(sqlite_connection).get(outcome.document_id)
    assert document is not None
    assert document.title == "Implement signup"
    assert document.source == "clickup"
    assert document.source_reference == task.id

    job = SqliteIngestionJobRepository(sqlite_connection).get_by_source(
        workspace_id, "clickup", task.id
    )
    assert job is not None
    assert job.source_identifier == task.id
    assert job.external_url == task.url

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        document_version = unit_of_work.document_versions.latest_for_document(document.id)
    assert document_version is not None
    assert document_version.body_markdown == "Implement signup\n\nAdd OAuth login"


def test_save_raw_import_with_a_url_in_the_description_stays_a_task_document(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for review finding S10-F01: a URL inside the task's own prose must not turn
    the task into an `ARTICLE` Resource -- the task is captured deterministically as its own
    raw-inbox Document with the task id on `source_reference`, never re-parsed as a channel
    message."""
    task = _task(description="Read https://arxiv.org/abs/2401.00001 then implement it")
    clickup_service, workspace_id = _clickup_service(sqlite_connection, tasks={task.id: task})

    outcome, plan_outcome = clickup_service.import_item(
        workspace_id, task_id=task.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )

    assert outcome.document_id is not None
    assert outcome.resource_id is None
    assert not outcome.needs_clarification
    assert plan_outcome is None

    document = SqliteDocumentRepository(sqlite_connection).get(outcome.document_id)
    assert document is not None
    assert document.title == "Implement signup"
    assert document.source_reference == task.id
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        version = unit_of_work.document_versions.latest_for_document(document.id)
    assert version is not None
    assert (
        version.body_markdown
        == "Implement signup\n\nRead https://arxiv.org/abs/2401.00001 then implement it"
    )


def test_plan_work_import_plans_only_when_justified_and_reuses_the_capture_pipeline(
    sqlite_connection: sqlite3.Connection,
) -> None:
    task = _task()
    planning_service = _work_planning_service(sqlite_connection)
    clickup_service, workspace_id = _clickup_service(
        sqlite_connection, tasks={task.id: task}, work_planning_service=planning_service
    )

    outcome, plan_outcome = clickup_service.import_item(
        workspace_id, task_id=task.id, intent=CaptureIntent.PLAN_WORK, actor_name="alperen"
    )

    assert [op.kind for op in outcome.pending_operations] == [CaptureOperationKind.PLAN]
    assert plan_outcome is not None
    assert plan_outcome.epic is not None


def test_reimporting_the_same_task_id_replays_without_duplicating_and_advances_the_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    task = _task()
    clickup_service, workspace_id = _clickup_service(sqlite_connection, tasks={task.id: task})

    first = clickup_service.import_item(
        workspace_id, task_id=task.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )
    second = clickup_service.import_item(
        workspace_id, task_id=task.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )

    assert first[0].document_id == second[0].document_id
    assert second[0].was_replayed is True
    assert first[1] is None and second[1] is None

    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, CLICKUP_CHANNEL_NAME
    )
    assert cursor is not None
    assert cursor.cursor_value == task.date_updated.isoformat()


def test_reimporting_a_task_with_changed_content_is_a_typed_conflict(
    sqlite_connection: sqlite3.Connection,
) -> None:
    original = _task()
    fake_client = FakeClickUpClient({original.id: original})
    clickup_service, workspace_id = _clickup_service(sqlite_connection, client=fake_client)

    clickup_service.import_item(
        workspace_id, task_id=original.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )
    fake_client._tasks[original.id] = _task(name="Completely different name")
    with pytest.raises(CaptureIdempotencyConflictError):
        clickup_service.import_item(
            workspace_id, task_id=original.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
        )


def test_a_later_import_with_an_older_date_updated_does_not_regress_the_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for review finding S10-F02: the cursor is monotonic, so importing a task
    updated earlier must never move the stored low-water mark backwards."""
    newer = _task(id="task-newer", date_updated=datetime(2026, 8, 10, 12, 0, tzinfo=UTC))
    older = _task(id="task-older", date_updated=datetime(2026, 8, 1, 12, 0, tzinfo=UTC))
    clickup_service, workspace_id = _clickup_service(
        sqlite_connection, tasks={newer.id: newer, older.id: older}
    )

    clickup_service.import_item(
        workspace_id, task_id=newer.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )
    clickup_service.import_item(
        workspace_id, task_id=older.id, intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
    )

    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, CLICKUP_CHANNEL_NAME
    )
    assert cursor is not None
    assert cursor.cursor_value == newer.date_updated.isoformat()


def test_unknown_task_raises_a_typed_not_found_error(
    sqlite_connection: sqlite3.Connection,
) -> None:
    clickup_service, workspace_id = _clickup_service(sqlite_connection, tasks={})

    with pytest.raises(ClickUpTaskNotFoundError):
        clickup_service.import_item(
            workspace_id, task_id="missing", intent=CaptureIntent.SAVE_RAW, actor_name="alperen"
        )


def test_unconfigured_client_fails_closed(sqlite_connection: sqlite3.Connection) -> None:
    clickup_service, _workspace_id = _clickup_service(sqlite_connection, client=None)

    with pytest.raises(ClickUpNotConfiguredError):
        clickup_service.import_item(
            WorkspaceId("ws"), task_id="task-1", intent=CaptureIntent.SAVE_RAW, actor_name="me"
        )
