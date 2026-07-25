from __future__ import annotations

import sqlite3

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.domain.capture import CaptureEnvelope, CaptureIntent, CapturePayloadKind
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.work_planning.fake_provider import FakeWorkPlanningProvider


class _RecordingProvider(FakeWorkPlanningProvider):
    def __init__(self, *, is_justified: bool = True) -> None:
        super().__init__(is_justified=is_justified)
        self.calls = 0

    def classify(self, *, source_text: str, title: str):  # type: ignore[override]
        self.calls += 1
        return super().classify(source_text=source_text, title=title)


def _orchestrator(
    sqlite_connection: sqlite3.Connection, *, work_planning_service: WorkPlanningService | None
) -> tuple[CapturePlanningOrchestrator, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    resource_service = ResourceService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteResourceRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    capture_service = CaptureService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteIdempotencyReceiptRepository(sqlite_connection),
        resource_service,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    orchestrator = CapturePlanningOrchestrator(
        capture_service,
        work_planning_service,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return orchestrator, workspace.id


def _work_planning_service(
    sqlite_connection: sqlite3.Connection, provider: FakeWorkPlanningProvider
) -> WorkPlanningService:
    work_item_service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return WorkPlanningService(
        work_item_service, provider, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )


def test_plan_work_with_a_justified_result_creates_the_hierarchy_once(
    sqlite_connection: sqlite3.Connection,
) -> None:
    planning_service = _work_planning_service(sqlite_connection, FakeWorkPlanningProvider())
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection, work_planning_service=planning_service
    )

    outcome, plan_outcome = orchestrator.submit(
        CaptureEnvelope(
            workspace_id=workspace_id,
            source="manual",
            request_id="req-1",
            actor_name="agent:test",
            payload_kind=CapturePayloadKind.TEXT,
            text="plan this",
            intent=CaptureIntent.PLAN_WORK,
        )
    )

    assert outcome.document_id is not None
    assert plan_outcome is not None
    assert len(plan_outcome.stories) == 1
    assert len(plan_outcome.tasks) == 1


def test_plan_work_with_an_unjustified_result_leaves_only_the_raw_capture(
    sqlite_connection: sqlite3.Connection,
) -> None:
    planning_service = _work_planning_service(
        sqlite_connection, FakeWorkPlanningProvider(is_justified=False)
    )
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection, work_planning_service=planning_service
    )

    outcome, plan_outcome = orchestrator.submit(
        CaptureEnvelope(
            workspace_id=workspace_id,
            source="manual",
            request_id="req-1",
            actor_name="agent:test",
            payload_kind=CapturePayloadKind.TEXT,
            text="plan this",
            intent=CaptureIntent.PLAN_WORK,
        )
    )

    assert outcome.document_id is not None
    assert plan_outcome is None
    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace_id) == ()


def test_save_raw_never_invokes_the_planning_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    provider = _RecordingProvider()
    planning_service = _work_planning_service(sqlite_connection, provider)
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection, work_planning_service=planning_service
    )

    outcome, plan_outcome = orchestrator.submit(
        CaptureEnvelope(
            workspace_id=workspace_id,
            source="manual",
            request_id="req-1",
            actor_name="agent:test",
            payload_kind=CapturePayloadKind.TEXT,
            text="just a note, nothing more",
            intent=CaptureIntent.SAVE_RAW,
        )
    )

    assert outcome.document_id is not None
    assert plan_outcome is None
    assert provider.calls == 0


def test_plan_operation_is_ignored_when_no_provider_is_configured(
    sqlite_connection: sqlite3.Connection,
) -> None:
    orchestrator, workspace_id = _orchestrator(sqlite_connection, work_planning_service=None)

    outcome, plan_outcome = orchestrator.submit(
        CaptureEnvelope(
            workspace_id=workspace_id,
            source="manual",
            request_id="req-1",
            actor_name="agent:test",
            payload_kind=CapturePayloadKind.TEXT,
            text="plan this",
            intent=CaptureIntent.PLAN_WORK,
        )
    )

    assert outcome.document_id is not None
    assert outcome.pending_operations
    assert plan_outcome is None


def test_replaying_the_same_capture_request_reuses_the_original_plan(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S5-R04: an identical `(workspace_id, source, actor_name, request_id)`
    resubmission must invoke the provider at most once and never create a second Epic/Story/Task
    tree, even though Capture's own idempotent replay still reports the same pending `plan`
    operation on the second call."""
    provider = _RecordingProvider()
    planning_service = _work_planning_service(sqlite_connection, provider)
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection, work_planning_service=planning_service
    )
    envelope = CaptureEnvelope(
        workspace_id=workspace_id,
        source="manual",
        request_id="req-1",
        actor_name="agent:test",
        payload_kind=CapturePayloadKind.TEXT,
        text="plan this",
        intent=CaptureIntent.PLAN_WORK,
    )

    first_outcome, first_plan = orchestrator.submit(envelope)
    second_outcome, second_plan = orchestrator.submit(envelope)

    assert first_outcome.job.id == second_outcome.job.id
    assert second_outcome.was_replayed is True
    assert provider.calls == 1
    assert first_plan is not None and second_plan is not None
    assert first_plan.epic.id == second_plan.epic.id
    assert len(
        SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace_id)
    ) == 1 + len(first_plan.stories) + len(first_plan.tasks)


def test_replaying_an_unjustified_plan_request_does_not_reinvoke_the_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S5-R04's remaining gap: a "do not plan" decision is just as terminal as a
    created hierarchy, so a replay of the identical request must not call the provider again."""
    provider = _RecordingProvider(is_justified=False)
    planning_service = _work_planning_service(sqlite_connection, provider)
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection, work_planning_service=planning_service
    )
    envelope = CaptureEnvelope(
        workspace_id=workspace_id,
        source="manual",
        request_id="req-1",
        actor_name="agent:test",
        payload_kind=CapturePayloadKind.TEXT,
        text="plan this",
        intent=CaptureIntent.PLAN_WORK,
    )

    first_outcome, first_plan = orchestrator.submit(envelope)
    second_outcome, second_plan = orchestrator.submit(envelope)

    assert first_outcome.job.id == second_outcome.job.id
    assert second_outcome.was_replayed is True
    assert provider.calls == 1
    assert first_plan is None
    assert second_plan is None
    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace_id) == ()
