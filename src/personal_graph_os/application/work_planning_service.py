"""Orchestrates one `plan_work` request end to end (EP-2026-012 ST-05): classify captured text,
and -- only when the classifier says a hierarchy is actually justified -- persist an Epic, its
Stories, each Story's Tasks, and the executor-neutral plan as one versioned Wiki `Document`
linked to the Epic, all atomically through one `ResearchUnitOfWork`.

`save_raw` never reaches this service at all (ST-02's `CaptureEnvelope` validator already
rejects any `operations` when `intent` is `save_raw`); `WorkPlanningService` only ever runs for
an explicit `plan_work` request.

Planning is idempotent per `ingestion_job_id` (review finding S5-R04): a captured job's identity
is stable across a Capture replay (`CaptureService`'s own idempotent commit always resolves a
retried request to the same job), so a `WorkPlanningReceipt` keyed on that job lets a repeated
`plan_from_capture` call for the same job reuse the original hierarchy/document instead of
invoking the provider again or creating a second Epic/Story/Task tree.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_graph_os.application.repositories import SearchIndexRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.work_item_service import WorkItemService
from personal_graph_os.application.work_planning_adapters import WorkPlanningProvider
from personal_graph_os.domain.documents import (
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
    DocumentVersion,
)
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import IngestionJobId, NodeId, WorkspaceId
from personal_graph_os.domain.search import (
    SearchEntityType,
    SearchScope,
    build_node_search_text,
)
from personal_graph_os.domain.work_items import WorkItem, WorkItemKind
from personal_graph_os.domain.work_planning import WorkPlanningReceipt, WorkPlanOutcomeStatus


class WorkPlanOutcome:
    """The result of one `plan_work` request that was actually justified."""

    def __init__(
        self,
        *,
        epic: WorkItem,
        stories: tuple[WorkItem, ...],
        tasks: tuple[WorkItem, ...],
        plan_document: Document,
        plan_version: DocumentVersion,
    ) -> None:
        self.epic = epic
        self.stories = stories
        self.tasks = tasks
        self.plan_document = plan_document
        self.plan_version = plan_version


class WorkPlanningService:
    def __init__(
        self,
        work_item_service: WorkItemService,
        provider: WorkPlanningProvider,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        search_index: SearchIndexRepository | None = None,
    ) -> None:
        self._work_item_service = work_item_service
        self._provider = provider
        self._unit_of_work_factory = unit_of_work_factory
        self._search_index = search_index

    def plan_from_capture(
        self,
        *,
        workspace_id: WorkspaceId,
        source_text: str,
        title: str,
        actor: str,
        repository_node_id: NodeId | None = None,
        ingestion_job_id: IngestionJobId | None = None,
    ) -> WorkPlanOutcome | None:
        """Classify `source_text`/`title` and, only if justified, persist the resulting
        hierarchy and plan Document as one atomic pass. Returns `None` when the classifier
        determined a hierarchy is not justified -- the caller's already-committed raw capture
        remains the only durable result in that case.

        `repository_node_id`, if given, must reference an existing Node in this workspace
        (review finding S5-R02): validated once before the provider call (a real network/model
        call should never run against input already known to be invalid), then propagated onto
        the Epic and every Story/Task it produces, and linked onto the plan Document alongside
        the Epic link -- the classifier itself never proposes or resolves it.

        `ingestion_job_id`, if given, makes this call idempotent (review finding S5-R04): a
        `WorkPlanningReceipt` already recorded for that job is reused -- reconstructed and
        returned without invoking the provider or creating anything -- instead of planning the
        same capture twice.
        """
        workspace = self._work_item_service.require_workspace(workspace_id)
        node_type = self._work_item_service.require_node_type(workspace)
        if repository_node_id is not None:
            with self._unit_of_work_factory() as unit_of_work:
                self._work_item_service.require_existing_node(
                    unit_of_work, workspace_id, repository_node_id
                )
        if ingestion_job_id is not None:
            with self._unit_of_work_factory() as unit_of_work:
                existing_receipt = unit_of_work.work_planning_receipts.get_by_ingestion_job(
                    ingestion_job_id
                )
                if existing_receipt is not None:
                    return self._reconstruct_outcome(unit_of_work, existing_receipt)

        result = self._provider.classify(source_text=source_text, title=title)
        if not result.is_justified:
            if ingestion_job_id is not None:
                with self._unit_of_work_factory() as unit_of_work:
                    existing_receipt = unit_of_work.work_planning_receipts.get_by_ingestion_job(
                        ingestion_job_id
                    )
                    if existing_receipt is not None:
                        return self._reconstruct_outcome(unit_of_work, existing_receipt)
                    unit_of_work.work_planning_receipts.save_without_commit(
                        WorkPlanningReceipt(
                            workspace_id=workspace_id,
                            ingestion_job_id=ingestion_job_id,
                            status=WorkPlanOutcomeStatus.NOT_JUSTIFIED,
                        )
                    )
            return None
        assert result.epic is not None
        assert result.plan_title is not None
        assert result.plan_body_markdown is not None

        created_nodes: list[Node] = []
        created_document: Document | None = None
        with self._unit_of_work_factory() as unit_of_work:
            if ingestion_job_id is not None:
                existing_receipt = unit_of_work.work_planning_receipts.get_by_ingestion_job(
                    ingestion_job_id
                )
                if existing_receipt is not None:
                    return self._reconstruct_outcome(unit_of_work, existing_receipt)

            epic, epic_node = self._work_item_service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=WorkItemKind.EPIC,
                work_type=result.epic.work_type,
                title=result.epic.title,
                body=result.epic.description,
                source=actor,
                repository_node_id=repository_node_id,
            )
            created_nodes.append(epic_node)

            stories: list[WorkItem] = []
            tasks: list[WorkItem] = []
            for proposed_story in result.stories:
                story, story_node = self._work_item_service.create_within(
                    unit_of_work,
                    workspace,
                    node_type,
                    kind=WorkItemKind.STORY,
                    work_type=proposed_story.work_type,
                    title=proposed_story.title,
                    body=proposed_story.description,
                    source=actor,
                    parent_id=epic.id,
                    repository_node_id=repository_node_id,
                )
                created_nodes.append(story_node)
                stories.append(story)
                for proposed_task in proposed_story.tasks:
                    task, task_node = self._work_item_service.create_within(
                        unit_of_work,
                        workspace,
                        node_type,
                        kind=WorkItemKind.TASK,
                        work_type=proposed_task.work_type,
                        title=proposed_task.title,
                        body=proposed_task.description,
                        source=actor,
                        parent_id=story.id,
                        repository_node_id=repository_node_id,
                    )
                    created_nodes.append(task_node)
                    tasks.append(task)

            plan_document = Document(
                workspace_id=workspace_id,
                kind=DocumentKind.PLAN,
                title=result.plan_title,
                source=actor,
            )
            created_document = plan_document
            plan_version = DocumentVersion(
                document_id=plan_document.id,
                version_number=1,
                body_markdown=result.plan_body_markdown,
                created_by=actor,
            )
            unit_of_work.documents.save_without_commit(plan_document)
            unit_of_work.document_versions.save_without_commit(plan_version)
            unit_of_work.document_links.save_without_commit(
                DocumentLink(
                    document_id=plan_document.id,
                    target_type=DocumentLinkTargetType.NODE,
                    target_id=epic_node.id,
                )
            )
            if repository_node_id is not None:
                unit_of_work.document_links.save_without_commit(
                    DocumentLink(
                        document_id=plan_document.id,
                        target_type=DocumentLinkTargetType.NODE,
                        target_id=repository_node_id,
                    )
                )
            if ingestion_job_id is not None:
                unit_of_work.work_planning_receipts.save_without_commit(
                    WorkPlanningReceipt(
                        workspace_id=workspace_id,
                        ingestion_job_id=ingestion_job_id,
                        status=WorkPlanOutcomeStatus.PLANNED,
                        epic_work_item_id=epic.id,
                        plan_document_id=plan_document.id,
                        plan_document_version_id=plan_version.id,
                    )
                )

            outcome = WorkPlanOutcome(
                epic=epic,
                stories=tuple(stories),
                tasks=tuple(tasks),
                plan_document=plan_document,
                plan_version=plan_version,
            )

        # Index after the unit of work commits (S12-F02 golden fixtures): the work items' nodes go
        # into the `tasks` scope and the plan document into `wiki`, so planned work is searchable.
        self._index_created_plan(workspace_id, created_nodes, created_document)
        return outcome

    def _index_created_plan(
        self,
        workspace_id: WorkspaceId,
        created_nodes: list[Node],
        created_document: Document | None,
    ) -> None:
        if self._search_index is None:
            return
        node_type = self._work_item_service.require_node_type(
            self._work_item_service.require_workspace(workspace_id)
        )
        for node in created_nodes:
            self._search_index.index_document(
                workspace_id=workspace_id,
                entity_type=SearchEntityType.NODE,
                entity_id=node.id,
                text=build_node_search_text(node, node_type),
                scope=SearchScope.TASKS,
            )
        if created_document is not None:
            self._search_index.index_document(
                workspace_id=workspace_id,
                entity_type=SearchEntityType.DOCUMENT,
                entity_id=created_document.id,
                text=created_document.title,
                scope=SearchScope.WIKI,
            )

    def _reconstruct_outcome(
        self, unit_of_work: ResearchUnitOfWork, receipt: WorkPlanningReceipt
    ) -> WorkPlanOutcome | None:
        """Replay a prior `plan_from_capture` call's terminal decision for the same
        `ingestion_job_id`, from its durable `WorkPlanningReceipt`, without invoking the provider
        or writing anything again. A `NOT_JUSTIFIED` receipt replays as `None` -- the original
        "do not plan" decision -- exactly like the first call's return value; only a `PLANNED`
        receipt has a hierarchy/document to rebuild."""
        if receipt.status is WorkPlanOutcomeStatus.NOT_JUSTIFIED:
            return None
        assert receipt.epic_work_item_id is not None
        assert receipt.plan_document_id is not None
        assert receipt.plan_document_version_id is not None
        epic = unit_of_work.work_items.get(receipt.epic_work_item_id)
        plan_document = unit_of_work.documents.get(receipt.plan_document_id)
        plan_version = unit_of_work.document_versions.get(receipt.plan_document_version_id)
        assert epic is not None
        assert plan_document is not None
        assert plan_version is not None
        stories = unit_of_work.work_items.list_by_parent(epic.id)
        tasks = tuple(
            task for story in stories for task in unit_of_work.work_items.list_by_parent(story.id)
        )
        return WorkPlanOutcome(
            epic=epic,
            stories=stories,
            tasks=tasks,
            plan_document=plan_document,
            plan_version=plan_version,
        )
