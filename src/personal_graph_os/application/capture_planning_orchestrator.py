"""Bridges Capture's recorded operations into execution (EP-2026-012 ST-05, S11-F05):
`CaptureService.submit()` only ever records operations as pending, never executes them (ST-02's
own scope boundary). This is the one composition point that actually runs them for a channel
request, using the just-captured Resource's own content:

- a `plan` operation (ST-05) reaches `WorkPlanningService` when a provider is configured;
- `summarize`/`extract key findings`/`relate to` operations (S11-F05) reach agentic enrichment
  (`ExtractionService` + `EnrichmentService`) when both are configured and the capture produced a
  Resource, so acceptance (2) -- "explicit enrich phrases → profile/Wiki/edges" -- is observably
  met instead of only recorded.

`save_raw` never reaches either executor: `CaptureEnvelope`'s own validator already rejects any
`operations` when `intent` is `save_raw` (ST-02), so `outcome.pending_operations` can never
contain an entry for one -- this orchestrator's own gate on that list is a second, structural
confirmation of the same invariant, not a new one.

Fail-soft for both executors: if no provider is configured, or extraction/enrichment fails, the
pending operation is left exactly as `CaptureService` recorded it (still pending, not executed) --
an unconfigured or failing deployment behaves like it did before the executor existed, never a
hard failure for a request that only implicitly asked for the work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from personal_graph_os.application.capture_service import CaptureOutcome, CaptureService
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.work_planning_service import WorkPlanningService, WorkPlanOutcome
from personal_graph_os.domain.capture import CaptureEnvelope, CaptureOperationKind
from personal_graph_os.domain.enrichment import EnrichmentError
from personal_graph_os.domain.extraction import ExtractionError
from personal_graph_os.domain.identifiers import NodeId

logger = logging.getLogger(__name__)

_ENRICHMENT_OPERATION_KINDS = frozenset(
    {
        CaptureOperationKind.SUMMARIZE,
        CaptureOperationKind.EXTRACT_KEY_FINDINGS,
        CaptureOperationKind.RELATE_TO,
    }
)


class CapturePlanningOrchestrator:
    def __init__(
        self,
        capture_service: CaptureService,
        work_planning_service: WorkPlanningService | None,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        enrichment_service: EnrichmentService | None = None,
        extraction_service: ExtractionService | None = None,
    ) -> None:
        self._capture_service = capture_service
        self._work_planning_service = work_planning_service
        self._enrichment_service = enrichment_service
        self._extraction_service = extraction_service
        self._unit_of_work_factory = unit_of_work_factory

    def submit(
        self, envelope: CaptureEnvelope, *, repository_node_id: NodeId | None = None
    ) -> tuple[CaptureOutcome, WorkPlanOutcome | None]:
        """Submit `envelope` through `CaptureService`, then execute any pending operations:
        agentic enrichment for `summarize`/`extract key findings`/`relate to`, then planning for
        `plan`, each only when the matching service is configured. Returns `(capture_outcome,
        plan_outcome)`; `plan_outcome` is `None` when planning did not run at all (not
        requested, not configured, or determined not justified). Enrichment is a persisted side
        effect, never part of the returned tuple."""
        outcome = self._capture_service.submit(envelope)
        self._maybe_run_enrichment(envelope, outcome)
        plan_outcome = self._maybe_run_planning(envelope, outcome, repository_node_id)
        return outcome, plan_outcome

    def _maybe_run_enrichment(self, envelope: CaptureEnvelope, outcome: CaptureOutcome) -> None:
        """Run agentic enrichment for a pending `summarize`/`extract key findings`/`relate to`
        operation when enrichment and extraction are configured and the capture produced a
        Resource (only resources are agentically enriched; ST-04). Fail-soft (S11-F05): an
        extraction or enrichment failure leaves the operation pending without failing the
        already-committed capture.

        Never runs on a replay (S11-F07): a redelivered update replays the capture
        (`was_replayed=True`) with the same `request_id`, and re-running enrichment would persist
        a second profile version and re-propose relations -- version/cost pollution that breaks
        acceptance (3) "replay idempotent". The first (non-replayed) delivery already enriched.
        """
        if outcome.was_replayed:
            return
        if self._enrichment_service is None or self._extraction_service is None:
            return
        if not any(
            operation.kind in _ENRICHMENT_OPERATION_KINDS
            for operation in outcome.pending_operations
        ):
            return
        if outcome.resource_id is None:
            return
        with self._unit_of_work_factory() as unit_of_work:
            resource = unit_of_work.resources.get(outcome.resource_id)
        if resource is None:
            return
        try:
            extracted = self._extraction_service.extract(
                resource_kind=resource.kind,
                canonical_identifier=resource.canonical_identifier,
                source_url=resource.source_url,
            )
        except ExtractionError as error:
            logger.warning(
                "capture %s: enrichment skipped, extraction failed for resource %s (%s)",
                envelope.request_id,
                outcome.resource_id,
                error,
            )
            return
        try:
            self._enrichment_service.enrich_resource(
                workspace_id=envelope.workspace_id,
                resource_id=resource.id,
                extracted=extracted,
                actor=envelope.actor_name,
            )
        except EnrichmentError as error:
            logger.warning(
                "capture %s: enrichment failed for resource %s and stays pending (%s)",
                envelope.request_id,
                outcome.resource_id,
                error,
            )

    def _maybe_run_planning(
        self,
        envelope: CaptureEnvelope,
        outcome: CaptureOutcome,
        repository_node_id: NodeId | None,
    ) -> WorkPlanOutcome | None:
        if self._work_planning_service is None:
            return None
        if not any(
            operation.kind is CaptureOperationKind.PLAN for operation in outcome.pending_operations
        ):
            return None

        with self._unit_of_work_factory() as unit_of_work:
            source_text, title = self._resolve_source_text_and_title(unit_of_work, outcome)
        if source_text is None or title is None:
            return None

        return self._work_planning_service.plan_from_capture(
            workspace_id=envelope.workspace_id,
            source_text=source_text,
            title=title,
            actor=envelope.actor_name,
            repository_node_id=repository_node_id,
            ingestion_job_id=outcome.job.id,
        )

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
