from __future__ import annotations

import sqlite3

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIntent,
    CaptureOperation,
    CaptureOperationKind,
    CapturePayloadKind,
)
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    hash_content,
)
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.enrichment.fake_provider import FakeEnrichmentProvider
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceEnrichmentProfileRepository,
    SqliteResourceRepository,
    SqliteWorkItemChecklistItemRepository,
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
    sqlite_connection: sqlite3.Connection,
    *,
    work_planning_service: WorkPlanningService | None,
    enrichment_service: EnrichmentService | None = None,
    extraction_service: object | None = None,
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
        enrichment_service=enrichment_service,
        extraction_service=extraction_service,  # type: ignore[arg-type]
    )
    return orchestrator, workspace.id


def _work_planning_service(
    sqlite_connection: sqlite3.Connection, provider: FakeWorkPlanningProvider
) -> WorkPlanningService:
    work_item_service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
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


class _StubExtractionService:
    """Stands in for `ExtractionService` (a separately tested, ST-03 concern): returns a fixed
    `ExtractedContent` for whatever resource is asked about, so this test proves the
    capture->enrichment orchestration wiring (S11-F05) without real network extraction."""

    def extract(
        self, *, resource_kind: ResourceKind, canonical_identifier: str, source_url: str | None
    ) -> ExtractedContent:
        return ExtractedContent(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            title="A Test Article",
            abstract="An abstract for the captured article.",
            evidence=(
                ExtractionEvidence(
                    kind=EvidenceKind.METADATA_LOOKUP,
                    adapter_name="test-adapter",
                    source_reference=canonical_identifier,
                    content_hash=hash_content(f"evidence-for-{canonical_identifier}"),
                    byte_length=32,
                ),
            ),
        )


def _enrichment_service(sqlite_connection: sqlite3.Connection) -> EnrichmentService:
    return EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        FakeEnrichmentProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )


def _summarize_envelope(workspace_id: WorkspaceId) -> CaptureEnvelope:
    return CaptureEnvelope(
        workspace_id=workspace_id,
        source="manual",
        request_id="req-summarize-1",
        actor_name="alperen",
        payload_kind=CapturePayloadKind.URL,
        url="https://example.com/article",
        intent=CaptureIntent.ENRICH,
        operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
    )


def test_a_summarize_operation_runs_enrichment_and_persists_a_profile(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F05: an explicit `summarize` operation is executed, not just recorded --
    acceptance (2) is observably met: the captured Article gets an enrichment profile."""
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection,
        work_planning_service=None,
        enrichment_service=_enrichment_service(sqlite_connection),
        extraction_service=_StubExtractionService(),
    )

    outcome, plan_outcome = orchestrator.submit(_summarize_envelope(workspace_id))

    assert outcome.resource_id is not None
    assert plan_outcome is None
    resource = SqliteResourceRepository(sqlite_connection).get(outcome.resource_id)
    assert resource is not None
    profile = SqliteResourceEnrichmentProfileRepository(sqlite_connection).get_by_identifier(
        workspace_id, resource.canonical_identifier
    )
    assert profile is not None
    assert profile.current_version_number == 1


def test_a_summarize_operation_stays_pending_when_enrichment_is_unconfigured(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Fail-soft (S11-F05): without an enrichment provider the capture still succeeds and the
    operation stays pending -- never a hard failure, mirroring the unconfigured planning path."""
    orchestrator, workspace_id = _orchestrator(sqlite_connection, work_planning_service=None)

    outcome, plan_outcome = orchestrator.submit(_summarize_envelope(workspace_id))

    assert outcome.resource_id is not None
    assert plan_outcome is None
    assert [operation.kind for operation in outcome.pending_operations] == [
        CaptureOperationKind.SUMMARIZE
    ]
    resource = SqliteResourceRepository(sqlite_connection).get(outcome.resource_id)
    assert resource is not None
    profile = SqliteResourceEnrichmentProfileRepository(sqlite_connection).get_by_identifier(
        workspace_id, resource.canonical_identifier
    )
    assert profile is None


def test_a_replayed_summarize_operation_does_not_re_run_enrichment(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F07: a redelivered update replays the capture (`was_replayed=True`) and
    must not persist a second enrichment profile version or re-propose relations -- acceptance (3)
    "replay idempotent" holds for the enrich path too."""
    orchestrator, workspace_id = _orchestrator(
        sqlite_connection,
        work_planning_service=None,
        enrichment_service=_enrichment_service(sqlite_connection),
        extraction_service=_StubExtractionService(),
    )
    envelope = _summarize_envelope(workspace_id)

    first_outcome, _first_plan = orchestrator.submit(envelope)
    second_outcome, _second_plan = orchestrator.submit(envelope)

    assert first_outcome.was_replayed is False
    assert second_outcome.was_replayed is True
    assert first_outcome.resource_id is not None
    resource = SqliteResourceRepository(sqlite_connection).get(first_outcome.resource_id)
    assert resource is not None
    profile = SqliteResourceEnrichmentProfileRepository(sqlite_connection).get_by_identifier(
        workspace_id, resource.canonical_identifier
    )
    assert profile is not None
    assert profile.current_version_number == 1
