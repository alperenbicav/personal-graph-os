from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.application.work_item_service import (
    RepositoryNodeNotFoundError,
    WorkItemService,
)
from personal_graph_os.application.work_planning_service import WorkPlanningService
from personal_graph_os.domain.documents import DocumentLinkTargetType
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import IngestionJobId, NodeId
from personal_graph_os.domain.ingestion import IngestionJob
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.work_items import WorkItemKind, WorkItemType
from personal_graph_os.domain.work_planning import WorkPlanOutcomeStatus
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkPlanningReceiptRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.work_planning.fake_provider import FakeWorkPlanningProvider


def _services(sqlite_connection: sqlite3.Connection) -> tuple[WorkItemService, Workspace]:
    workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Personal")))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    work_item_service = WorkItemService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
        SqliteWorkItemChecklistItemRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    return work_item_service, workspace


def test_plan_from_capture_creates_epic_stories_and_tasks(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    provider = FakeWorkPlanningProvider(
        story_titles=("Story A", "Story B"), task_titles_per_story=("Task 1", "Task 2")
    )
    planning_service = WorkPlanningService(
        work_item_service, provider, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="A large multi-step effort worth planning.",
        title="Ship the feature",
        actor="agent:test",
    )

    assert outcome is not None
    assert outcome.epic.kind is WorkItemKind.EPIC
    assert outcome.epic.parent_id is None
    assert len(outcome.stories) == 2
    assert all(story.parent_id == outcome.epic.id for story in outcome.stories)
    assert len(outcome.tasks) == 4
    story_ids = {story.id for story in outcome.stories}
    assert all(task.parent_id in story_ids for task in outcome.tasks)


def test_plan_from_capture_persists_the_hierarchy(sqlite_connection: sqlite3.Connection) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
    )
    assert outcome is not None

    work_items = SqliteWorkItemRepository(sqlite_connection)
    assert work_items.get(outcome.epic.id) is not None
    for story in outcome.stories:
        assert work_items.get(story.id) is not None
    for task in outcome.tasks:
        assert work_items.get(task.id) is not None


def test_plan_from_capture_creates_a_versioned_plan_document_linked_to_the_epic(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
    )
    assert outcome is not None

    unit_of_work = SqliteResearchUnitOfWork(sqlite_connection)
    document = unit_of_work.documents.get(outcome.plan_document.id)
    assert document is not None
    version = unit_of_work.document_versions.latest_for_document(document.id)
    assert version is not None
    assert version.version_number == 1
    assert version.body_markdown == outcome.plan_version.body_markdown

    links = unit_of_work.document_links.list_by_document(document.id)
    assert len(links) == 1
    assert links[0].target_type is DocumentLinkTargetType.NODE
    assert links[0].target_id == outcome.epic.node_id


def test_plan_from_capture_returns_none_when_not_justified(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(is_justified=False),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="A single throwaway line.",
        title="Quick note",
        actor="agent:test",
    )

    assert outcome is None
    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace.id) == ()


def _seed_ingestion_job(
    sqlite_connection: sqlite3.Connection, workspace: Workspace
) -> IngestionJobId:
    job = IngestionJob(workspace_id=workspace.id, source="manual", source_identifier="req-1")
    SqliteIngestionJobRepository(sqlite_connection).save(job)
    return job.id


class _CountingProvider(FakeWorkPlanningProvider):
    def __init__(self, *, is_justified: bool) -> None:
        super().__init__(is_justified=is_justified)
        self.calls = 0

    def classify(self, *, source_text: str, title: str):  # type: ignore[override]
        self.calls += 1
        return super().classify(source_text=source_text, title=title)


def test_plan_from_capture_persists_a_not_justified_receipt(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    ingestion_job_id = _seed_ingestion_job(sqlite_connection, workspace)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(is_justified=False),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="A single throwaway line.",
        title="Quick note",
        actor="agent:test",
        ingestion_job_id=ingestion_job_id,
    )

    assert outcome is None
    receipt = SqliteWorkPlanningReceiptRepository(sqlite_connection).get_by_ingestion_job(
        ingestion_job_id
    )
    assert receipt is not None
    assert receipt.status is WorkPlanOutcomeStatus.NOT_JUSTIFIED
    assert receipt.epic_work_item_id is None
    assert receipt.plan_document_id is None
    assert receipt.plan_document_version_id is None


def test_replaying_an_unjustified_request_does_not_reinvoke_the_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    ingestion_job_id = _seed_ingestion_job(sqlite_connection, workspace)
    provider = _CountingProvider(is_justified=False)
    planning_service = WorkPlanningService(
        work_item_service, provider, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )

    first = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="A single throwaway line.",
        title="Quick note",
        actor="agent:test",
        ingestion_job_id=ingestion_job_id,
    )
    second = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="A single throwaway line.",
        title="Quick note",
        actor="agent:test",
        ingestion_job_id=ingestion_job_id,
    )

    assert first is None
    assert second is None
    assert provider.calls == 1
    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace.id) == ()


def test_replaying_a_justified_request_reuses_the_receipt_and_does_not_reinvoke_the_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    ingestion_job_id = _seed_ingestion_job(sqlite_connection, workspace)
    provider = _CountingProvider(is_justified=True)
    planning_service = WorkPlanningService(
        work_item_service, provider, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )

    first = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
        ingestion_job_id=ingestion_job_id,
    )
    second = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
        ingestion_job_id=ingestion_job_id,
    )

    assert first is not None and second is not None
    assert provider.calls == 1
    assert first.epic.id == second.epic.id
    assert len(
        SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace.id)
    ) == 1 + len(first.stories) + len(first.tasks)


def test_a_concurrent_unjustified_race_persists_exactly_one_receipt(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Recovery/race guard for review finding S5-R04: two overlapping `plan_from_capture` calls
    for the same job (e.g. a crash-recovery retry racing a still-running original call) must
    still leave exactly one terminal receipt and invoke the provider for each concurrent attempt
    that started before either one committed -- but never diverge into two different terminal
    decisions or two hierarchies for the same job."""
    work_item_service, workspace = _services(sqlite_connection)
    ingestion_job_id = _seed_ingestion_job(sqlite_connection, workspace)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(is_justified=False),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcomes = [
        planning_service.plan_from_capture(
            workspace_id=workspace.id,
            source_text="A single throwaway line.",
            title="Quick note",
            actor="agent:test",
            ingestion_job_id=ingestion_job_id,
        )
        for _ in range(2)
    ]

    assert outcomes == [None, None]
    receipts = sqlite_connection.execute(
        "SELECT COUNT(*) FROM work_planning_receipts WHERE ingestion_job_id = ?",
        (ingestion_job_id,),
    ).fetchone()[0]
    assert receipts == 1


def test_plan_from_capture_uses_the_proposed_work_type(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(work_type=WorkItemType.RESEARCH),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Investigate an approach.",
        title="Research spike",
        actor="agent:test",
    )

    assert outcome is not None
    assert outcome.epic.work_type is WorkItemType.RESEARCH
    assert all(story.work_type is WorkItemType.RESEARCH for story in outcome.stories)
    assert all(task.work_type is WorkItemType.RESEARCH for task in outcome.tasks)


def _create_repository_node(sqlite_connection: sqlite3.Connection, workspace: Workspace) -> Node:
    node_type = workspace.node_types[0]
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="apilex-agent")
    SqliteNodeRepository(sqlite_connection).save(node)
    return node


def test_plan_from_capture_propagates_repository_node_id_to_the_whole_hierarchy(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    repository_node = _create_repository_node(sqlite_connection, workspace)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
        repository_node_id=repository_node.id,
    )

    assert outcome is not None
    assert outcome.epic.repository_node_id == repository_node.id
    assert all(story.repository_node_id == repository_node.id for story in outcome.stories)
    assert all(task.repository_node_id == repository_node.id for task in outcome.tasks)


def test_plan_from_capture_links_the_plan_document_to_the_repository_too(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    repository_node = _create_repository_node(sqlite_connection, workspace)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    outcome = planning_service.plan_from_capture(
        workspace_id=workspace.id,
        source_text="Some substantial work.",
        title="Do the thing",
        actor="agent:test",
        repository_node_id=repository_node.id,
    )
    assert outcome is not None

    unit_of_work = SqliteResearchUnitOfWork(sqlite_connection)
    links = unit_of_work.document_links.list_by_document(outcome.plan_document.id)
    target_ids = {link.target_id for link in links}
    assert target_ids == {outcome.epic.node_id, repository_node.id}


def test_plan_from_capture_rejects_a_missing_repository_node_before_calling_the_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)

    class _FailIfCalledProvider(FakeWorkPlanningProvider):
        def classify(self, *, source_text: str, title: str):  # type: ignore[override]
            raise AssertionError("provider must not be called for an invalid repository_node_id")

    planning_service = WorkPlanningService(
        work_item_service,
        _FailIfCalledProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    with pytest.raises(RepositoryNodeNotFoundError):
        planning_service.plan_from_capture(
            workspace_id=workspace.id,
            source_text="Some substantial work.",
            title="Do the thing",
            actor="agent:test",
            repository_node_id=NodeId("does-not-exist"),
        )

    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace.id) == ()


def test_plan_from_capture_rejects_a_repository_node_from_a_different_workspace(
    sqlite_connection: sqlite3.Connection,
) -> None:
    work_item_service, workspace = _services(sqlite_connection)
    other_workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Other")))
    SqliteWorkspaceRepository(sqlite_connection).save(other_workspace)
    repository_node_in_other_workspace = _create_repository_node(sqlite_connection, other_workspace)
    planning_service = WorkPlanningService(
        work_item_service,
        FakeWorkPlanningProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    with pytest.raises(RepositoryNodeNotFoundError):
        planning_service.plan_from_capture(
            workspace_id=workspace.id,
            source_text="Some substantial work.",
            title="Do the thing",
            actor="agent:test",
            repository_node_id=repository_node_in_other_workspace.id,
        )

    assert SqliteWorkItemRepository(sqlite_connection).list_by_workspace(workspace.id) == ()
