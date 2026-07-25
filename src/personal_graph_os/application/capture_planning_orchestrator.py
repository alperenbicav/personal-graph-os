"""Bridges Capture's recorded `plan` operation into `WorkPlanningService` (EP-2026-012 ST-05,
review finding S5-R01): `CaptureService.submit()` only ever records a `plan` operation as
pending, never executes it (ST-02's own scope boundary); this is the one composition point that
actually runs planning for a `plan_work` request, using the just-captured Resource/Document's own
title and body as the planner's input.

`save_raw` never reaches the planner at all: `CaptureEnvelope`'s own validator already rejects
any `operations` when `intent` is `save_raw` (ST-02), so `outcome.pending_operations` can never
contain a `plan` entry for one -- this orchestrator's own gate on that list is a second,
structural confirmation of the same invariant, not a new one.

If no `WorkPlanningProvider` is configured, a pending `plan` operation is left exactly as
`CaptureService` recorded it (still pending, not executed) -- an unconfigured deployment behaves
identically to how it did before ST-05 added a real planner, never a hard failure for a request
that only implicitly asked for planning.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_graph_os.application.capture_service import CaptureOutcome, CaptureService
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.work_planning_service import WorkPlanningService, WorkPlanOutcome
from personal_graph_os.domain.capture import CaptureEnvelope, CaptureOperationKind
from personal_graph_os.domain.identifiers import NodeId


class CapturePlanningOrchestrator:
    def __init__(
        self,
        capture_service: CaptureService,
        work_planning_service: WorkPlanningService | None,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._capture_service = capture_service
        self._work_planning_service = work_planning_service
        self._unit_of_work_factory = unit_of_work_factory

    def submit(
        self, envelope: CaptureEnvelope, *, repository_node_id: NodeId | None = None
    ) -> tuple[CaptureOutcome, WorkPlanOutcome | None]:
        """Submit `envelope` through `CaptureService`, then run planning if -- and only if -- a
        `plan` operation is pending and a provider is configured. Returns `(capture_outcome,
        plan_outcome)`; `plan_outcome` is `None` when planning did not run at all (not
        requested, not configured, or determined not justified)."""
        outcome = self._capture_service.submit(envelope)
        if self._work_planning_service is None:
            return outcome, None
        if not any(
            operation.kind is CaptureOperationKind.PLAN for operation in outcome.pending_operations
        ):
            return outcome, None

        with self._unit_of_work_factory() as unit_of_work:
            source_text, title = self._resolve_source_text_and_title(unit_of_work, outcome)
        if source_text is None or title is None:
            return outcome, None

        plan_outcome = self._work_planning_service.plan_from_capture(
            workspace_id=envelope.workspace_id,
            source_text=source_text,
            title=title,
            actor=envelope.actor_name,
            repository_node_id=repository_node_id,
            ingestion_job_id=outcome.job.id,
        )
        return outcome, plan_outcome

    def _resolve_source_text_and_title(
        self, unit_of_work: ResearchUnitOfWork, outcome: CaptureOutcome
    ) -> tuple[str | None, str | None]:
        if outcome.resource_id is not None:
            resource = unit_of_work.resources.get(outcome.resource_id)
            if resource is None:
                return None, None
            node = unit_of_work.nodes.get(resource.node_id)
            if node is None:
                return None, None
            return (node.body or node.title), node.title
        if outcome.document_id is not None:
            document = unit_of_work.documents.get(outcome.document_id)
            if document is None:
                return None, None
            version = unit_of_work.document_versions.latest_for_document(document.id)
            if version is None:
                return None, None
            return version.body_markdown, document.title
        return None, None
