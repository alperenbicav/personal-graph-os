"""Channel-neutral capture intake (EP-2026-012 ST-02).

`CaptureService.submit()` is the single entry point every capture channel eventually calls with
a `CaptureEnvelope`. It decides, deterministically and without ever calling a model:

- replay an already-committed `IngestionJob` when `(workspace_id, source, request_id)` repeats;
- capture a `url` (or a `text` payload whose parsed message contains one) as a raw Research
  `Resource`, defaulting its kind to `article` unless a DOI/arXiv/GitHub identity is detected;
- capture anything else -- unparseable text, or a `file`/`external_item` payload extraction
  adapters (ST-03) do not exist for yet -- as a standalone raw-inbox `Document` carrying that
  source's own stable identifier, never discarding the caller's content or identity;
- record any recognized `enrich`/`plan_work` operation on the outcome as pending, without
  executing it -- ST-03 (extraction), ST-04 (enrichment/relations), and ST-05 (work planning) are
  separate stories. `save_raw` never derives or acts on any operation, however the free text reads
  (review finding S2-R02): an explicit `enrich`/`plan_work` intent is required before a caption or
  message body is even parsed for operations.

The captured entity, its `IngestionJob`, and an `IdempotencyReceipt` binding its content
fingerprint commit atomically in one transaction (review finding S2-R01): a crash anywhere before
that commit leaves no job or receipt row behind at all, so a retry with the same
`(workspace_id, source, request_id)` always starts completely fresh rather than resuming a
partially written capture. A found receipt is therefore always a terminal, committed replay --
but only when the retried envelope's own `content_fingerprint()` still matches the one that key
originally committed (mirroring `AgentGatewayService`'s `payload_fingerprint` convention); a
mismatch means the same key is being reused for a different command and is rejected as a
`CaptureIdempotencyConflictError` rather than silently pairing the old entity with new
instructions. `pending_operations`/`needs_clarification` are recomputed by re-running the same
deterministic interpretation against the retried (fingerprint-verified) envelope, not read back
from any separately stored state.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from personal_graph_os.application.repositories import (
    IdempotencyReceiptRepository,
    IngestionJobRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.services import ResourceService, WorkspaceNotFoundError
from personal_graph_os.domain.activity import IdempotencyReceipt
from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIdempotencyConflictError,
    CaptureIntent,
    CaptureOperation,
    CapturePayloadKind,
)
from personal_graph_os.domain.capture_parsing import ParsedCaptureText, parse_capture_text
from personal_graph_os.domain.documents import Document, DocumentKind, DocumentVersion
from personal_graph_os.domain.identifiers import DocumentId, ResourceId, WorkspaceId
from personal_graph_os.domain.ingestion import IngestionJob, IngestionStage
from personal_graph_os.domain.resource import Resource, ResourceKind
from personal_graph_os.domain.schema import Workspace

_CAPTURE_OPERATION = "capture_submit"
_DEFAULT_DOCUMENT_TITLE = "Untitled capture"
_STAGES_BEFORE_COMMITTED = (
    IngestionStage.NORMALIZED,
    IngestionStage.EXTRACTED,
    IngestionStage.ENRICHED,
    IngestionStage.LINKED,
)


class CaptureOutcome(BaseModel):
    """What one `CaptureService.submit()` call resolved to."""

    job: IngestionJob
    resource_id: ResourceId | None = None
    document_id: DocumentId | None = None
    pending_operations: tuple[CaptureOperation, ...] = ()
    needs_clarification: bool = False
    clarification_reason: str | None = None
    was_replayed: bool = False


class CaptureService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        ingestion_jobs: IngestionJobRepository,
        idempotency_receipts: IdempotencyReceiptRepository,
        resource_service: ResourceService,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._ingestion_jobs = ingestion_jobs
        self._idempotency_receipts = idempotency_receipts
        self._resource_service = resource_service
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def submit(self, envelope: CaptureEnvelope) -> CaptureOutcome:
        self._require_workspace(envelope.workspace_id)
        fingerprint = envelope.content_fingerprint()

        existing_receipt = self._idempotency_receipts.get_by_request(
            envelope.workspace_id, envelope.source, envelope.actor_name, envelope.request_id
        )
        source_url, operations, clarification_reason = self._interpret(envelope)

        if existing_receipt is not None:
            if existing_receipt.payload_fingerprint != fingerprint:
                raise CaptureIdempotencyConflictError(
                    f"request_id {envelope.request_id!r} on source {envelope.source!r} was "
                    "already used for a different capture command"
                )
            existing_job = self._ingestion_jobs.get_by_source(
                envelope.workspace_id, envelope.source, envelope.request_id
            )
            if existing_job is None:
                raise AssertionError(
                    "an IdempotencyReceipt exists without its matching IngestionJob -- both are "
                    "always written together in the same transaction"
                )
            return self._outcome_for_committed_job(
                existing_job,
                operations=operations,
                clarification_reason=clarification_reason,
                was_replayed=True,
            )

        if source_url is None:
            _document, job = self._capture_as_document(envelope, fingerprint=fingerprint)
            return self._outcome_for_committed_job(
                job,
                operations=operations,
                clarification_reason=clarification_reason,
                was_replayed=False,
            )

        _resource, job = self._capture_as_resource(envelope, source_url, fingerprint=fingerprint)
        return self._outcome_for_committed_job(
            job,
            operations=operations,
            clarification_reason=clarification_reason,
            was_replayed=False,
        )

    def _outcome_for_committed_job(
        self,
        job: IngestionJob,
        *,
        operations: tuple[CaptureOperation, ...],
        clarification_reason: str | None,
        was_replayed: bool,
    ) -> CaptureOutcome:
        resource_id = (
            ResourceId(job.result_entity_id)
            if job.result_entity_type == "resource" and job.result_entity_id is not None
            else None
        )
        is_document_fallback = job.result_entity_type == "document"
        document_id = (
            DocumentId(job.result_entity_id)
            if is_document_fallback and job.result_entity_id is not None
            else None
        )
        needs_clarification = is_document_fallback or clarification_reason is not None
        reason = clarification_reason
        if is_document_fallback and reason is None:
            reason = "no recognized URL or identifier found in capture text"
        return CaptureOutcome(
            job=job,
            resource_id=resource_id,
            document_id=document_id,
            pending_operations=operations,
            needs_clarification=needs_clarification,
            clarification_reason=reason,
            was_replayed=was_replayed,
        )

    def _interpret(
        self, envelope: CaptureEnvelope
    ) -> tuple[str | None, tuple[CaptureOperation, ...], str | None]:
        """Resolve `envelope` to `(source_url, operations, clarification_reason)`.

        `source_url` is `None` only when nothing capturable as a Research resource was found --
        the caller then falls back to a raw-inbox `Document`. `save_raw` never derives or reports
        operations, no matter what a `text`/caption contains (review finding S2-R02): it only ever
        contributes a URL to capture, never an instruction to act on.
        """
        if envelope.payload_kind is CapturePayloadKind.FILE:
            return None, envelope.operations, None
        if envelope.payload_kind is CapturePayloadKind.EXTERNAL_ITEM:
            return None, envelope.operations, None

        parsed: ParsedCaptureText | None
        if envelope.payload_kind is CapturePayloadKind.URL:
            source_url = envelope.url
            parsed = parse_capture_text(envelope.text) if envelope.text else None
        else:  # TEXT
            parsed = parse_capture_text(envelope.text or "")
            source_url = parsed.detected_url

        if envelope.intent is CaptureIntent.SAVE_RAW:
            return source_url, (), None

        operations = envelope.operations or (parsed.operations if parsed else ())
        unrecognized = parsed.unrecognized_segments if parsed else ()
        reason = f"could not interpret: {'; '.join(unrecognized)}" if unrecognized else None
        return source_url, operations, reason

    def _capture_as_resource(
        self, envelope: CaptureEnvelope, source_url: str, *, fingerprint: str
    ) -> tuple[Resource, IngestionJob]:
        title = envelope.title or source_url
        with self._unit_of_work_factory() as unit_of_work:
            resource, was_created, node = self._resource_service.create_or_reuse_within(
                unit_of_work,
                envelope.workspace_id,
                title,
                source_url,
                kind=ResourceKind.ARTICLE,
                body=envelope.text or "",
            )
            job = self._build_committed_job(envelope, entity_type="resource", entity_id=resource.id)
            unit_of_work.ingestion_jobs.save_without_commit(job)
            self._save_receipt(unit_of_work, envelope, fingerprint)
        if was_created and node is not None:
            self._resource_service.index_created_resource(resource, node)
        return resource, job

    def _capture_as_document(
        self, envelope: CaptureEnvelope, *, fingerprint: str
    ) -> tuple[Document, IngestionJob]:
        document = Document(
            workspace_id=envelope.workspace_id,
            kind=DocumentKind.NOTE,
            title=envelope.title or _default_document_title(envelope),
            source=envelope.source,
            source_reference=envelope.file_reference_id or envelope.external_item_id,
        )
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            body_markdown=envelope.text or "",
            created_by=envelope.actor_name,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.documents.save_without_commit(document)
            unit_of_work.document_versions.save_without_commit(version)
            job = self._build_committed_job(envelope, entity_type="document", entity_id=document.id)
            unit_of_work.ingestion_jobs.save_without_commit(job)
            self._save_receipt(unit_of_work, envelope, fingerprint)
        return document, job

    def _save_receipt(
        self, unit_of_work: ResearchUnitOfWork, envelope: CaptureEnvelope, fingerprint: str
    ) -> None:
        unit_of_work.idempotency_receipts.save_without_commit(
            IdempotencyReceipt(
                workspace_id=envelope.workspace_id,
                source=envelope.source,
                actor_name=envelope.actor_name,
                request_id=envelope.request_id,
                operation=_CAPTURE_OPERATION,
                payload_fingerprint=fingerprint,
                result_payload={},
            )
        )

    def _build_committed_job(
        self, envelope: CaptureEnvelope, *, entity_type: str, entity_id: str
    ) -> IngestionJob:
        job = IngestionJob(
            workspace_id=envelope.workspace_id,
            source=envelope.source,
            source_identifier=envelope.request_id,
        )
        for stage in _STAGES_BEFORE_COMMITTED:
            job = job.advance_to(stage)
        return job.advance_to(
            IngestionStage.COMMITTED, result_entity_type=entity_type, result_entity_id=entity_id
        )


def _default_document_title(envelope: CaptureEnvelope) -> str:
    if envelope.text:
        stripped = envelope.text.strip()
        if stripped:
            return stripped[:80]
    return _DEFAULT_DOCUMENT_TITLE
