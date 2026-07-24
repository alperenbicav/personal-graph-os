from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    ResourceService,
    WorkspaceNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIdempotencyConflictError,
    CaptureIntent,
    CaptureOperation,
    CaptureOperationKind,
    CapturePayloadKind,
)
from personal_graph_os.domain.identifiers import FileReferenceId, WorkspaceId, new_id
from personal_graph_os.domain.ingestion import IngestionStage
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _capture_service(
    sqlite_connection: sqlite3.Connection,
) -> tuple[CaptureService, ResourceService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    resource_service = ResourceService(
        workspace_repository, resource_repository, unit_of_work_factory
    )
    capture_service = CaptureService(
        workspace_repository,
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteIdempotencyReceiptRepository(sqlite_connection),
        resource_service,
        unit_of_work_factory,
    )
    return capture_service, resource_service, workspace.id


def _envelope(workspace_id: WorkspaceId, **overrides: object) -> CaptureEnvelope:
    fields: dict[str, object] = {
        "workspace_id": workspace_id,
        "source": "manual",
        "request_id": "req-1",
        "actor_name": "alperen",
        "payload_kind": CapturePayloadKind.URL,
        "url": "https://example.com/article",
    }
    fields.update(overrides)
    return CaptureEnvelope.model_validate(fields)


def test_raises_for_unknown_workspace(sqlite_connection: sqlite3.Connection) -> None:
    capture_service, _resource_service, _workspace_id = _capture_service(sqlite_connection)

    with pytest.raises(WorkspaceNotFoundError):
        capture_service.submit(_envelope(WorkspaceId(new_id())))


def test_url_only_capture_defaults_to_a_committed_raw_article_resource(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(_envelope(workspace_id))

    assert outcome.resource_id is not None
    assert outcome.document_id is None
    assert not outcome.needs_clarification
    assert outcome.job.stage is IngestionStage.COMMITTED
    assert outcome.job.result_entity_type == "resource"
    assert outcome.job.result_entity_id == outcome.resource_id
    resource = resource_service.get(outcome.resource_id)
    assert resource.kind is ResourceKind.ARTICLE


def test_url_with_doi_identity_overrides_the_default_article_kind(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(_envelope(workspace_id, url="https://doi.org/10.1000/xyz123"))

    assert outcome.resource_id is not None
    resource = resource_service.get(outcome.resource_id)
    assert resource.kind is ResourceKind.PAPER


def test_replaying_the_same_request_returns_the_same_committed_job(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    envelope = _envelope(workspace_id)

    first = capture_service.submit(envelope)
    second = capture_service.submit(envelope)

    assert first.was_replayed is False
    assert second.was_replayed is True
    assert second.resource_id == first.resource_id
    assert second.job.id == first.job.id


def test_a_different_request_id_creates_a_second_job_and_reuses_the_resource(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    first = capture_service.submit(_envelope(workspace_id, request_id="req-1"))
    second = capture_service.submit(_envelope(workspace_id, request_id="req-2"))

    assert first.job.id != second.job.id
    assert first.resource_id == second.resource_id


def test_reusing_a_request_id_with_a_different_url_is_rejected_as_a_conflict(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S2-R01 delta: reusing `(workspace_id, source, request_id)` for a different
    URL must never silently pair the first committed entity with the second command."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    capture_service.submit(_envelope(workspace_id, url="https://example.com/article-a"))

    with pytest.raises(CaptureIdempotencyConflictError):
        capture_service.submit(_envelope(workspace_id, url="https://example.com/article-b"))


def test_reusing_a_request_id_with_a_different_intent_is_rejected_as_a_conflict(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """The exact scenario the reviewer reproduced: `save_raw` for URL A, then the same key
    reused with `enrich`/`summarize` -- must be rejected, not silently return a hybrid result."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    capture_service.submit(_envelope(workspace_id, intent=CaptureIntent.SAVE_RAW))

    with pytest.raises(CaptureIdempotencyConflictError):
        capture_service.submit(
            _envelope(
                workspace_id,
                intent=CaptureIntent.ENRICH,
                operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
            )
        )


def test_explicit_enrich_operations_are_pending_not_executed(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            intent=CaptureIntent.ENRICH,
            operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
        )
    )

    assert outcome.resource_id is not None
    assert not outcome.needs_clarification
    assert outcome.pending_operations == (CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),)
    assert outcome.job.stage is IngestionStage.COMMITTED


def test_replaying_an_enrich_request_recomputes_the_same_pending_operations(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S2-R01: pending operations must survive a retry even though nothing
    stores them directly -- `_interpret` is pure, so replaying the identical envelope must
    reproduce identical `pending_operations`, not silently drop them."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    envelope = _envelope(
        workspace_id,
        intent=CaptureIntent.ENRICH,
        operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
    )

    first = capture_service.submit(envelope)
    second = capture_service.submit(envelope)

    assert second.was_replayed
    assert second.pending_operations == first.pending_operations
    assert second.resource_id == first.resource_id


def test_save_raw_text_never_derives_operations_even_when_the_words_appear(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S2-R02: `save_raw` must never infer enrichment/planning work, no matter
    what the free text says."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.TEXT,
            url=None,
            intent=CaptureIntent.SAVE_RAW,
            text="https://arxiv.org/abs/2401.00001 summarize, plan this",
        )
    )

    assert outcome.resource_id is not None
    assert not outcome.needs_clarification
    assert outcome.pending_operations == ()


def test_enrich_intent_parses_a_url_payloads_accompanying_caption(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S2-R02: an explicit `enrich`/`plan_work` intent must parse a `url`
    payload's accompanying caption text instead of silently dropping it."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            intent=CaptureIntent.ENRICH,
            text="summarize, extract key findings",
        )
    )

    assert outcome.resource_id is not None
    assert [operation.kind for operation in outcome.pending_operations] == [
        CaptureOperationKind.SUMMARIZE,
        CaptureOperationKind.EXTRACT_KEY_FINDINGS,
    ]


def test_text_payload_with_a_url_and_recognized_operations_captures_and_queues_them(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.TEXT,
            url=None,
            intent=CaptureIntent.ENRICH,
            text="https://arxiv.org/abs/2401.00001 summarize, extract key findings",
        )
    )

    assert outcome.resource_id is not None
    assert not outcome.needs_clarification
    assert [operation.kind for operation in outcome.pending_operations] == [
        CaptureOperationKind.SUMMARIZE,
        CaptureOperationKind.EXTRACT_KEY_FINDINGS,
    ]


def test_text_payload_with_an_unrecognized_segment_still_captures_but_asks_for_clarification(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.TEXT,
            url=None,
            intent=CaptureIntent.ENRICH,
            text="https://arxiv.org/abs/2401.00001 do something clever",
        )
    )

    assert outcome.resource_id is not None
    assert outcome.needs_clarification
    assert "do something clever" in (outcome.clarification_reason or "")


def test_text_payload_with_no_url_becomes_a_raw_inbox_document_requiring_clarification(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.TEXT,
            url=None,
            text="just a note to self, remember this later",
        )
    )

    assert outcome.resource_id is None
    assert outcome.document_id is not None
    assert outcome.needs_clarification
    assert outcome.job.stage is IngestionStage.COMMITTED
    assert outcome.job.result_entity_type == "document"


def test_file_payload_becomes_a_committed_document_carrying_its_source_reference(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S2-R03: the file's own stable identifier must be recoverable from the
    raw-inbox document afterward, not only from the ephemeral envelope."""
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    file_reference_id = FileReferenceId(new_id())

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.FILE,
            url=None,
            file_reference_id=file_reference_id,
        )
    )

    assert outcome.document_id is not None
    assert outcome.resource_id is None
    assert outcome.job.stage is IngestionStage.COMMITTED
    assert outcome.job.result_entity_type == "document"
    document = SqliteDocumentRepository(sqlite_connection).get(outcome.document_id)
    assert document is not None
    assert document.source_reference == file_reference_id


def test_external_item_payload_document_carries_its_source_reference(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)

    outcome = capture_service.submit(
        _envelope(
            workspace_id,
            payload_kind=CapturePayloadKind.EXTERNAL_ITEM,
            url=None,
            external_item_id="clickup-42",
        )
    )

    assert outcome.document_id is not None
    document = SqliteDocumentRepository(sqlite_connection).get(outcome.document_id)
    assert document is not None
    assert document.source_reference == "clickup-42"


def test_replaying_a_document_outcome_reports_the_same_document_id(
    sqlite_connection: sqlite3.Connection,
) -> None:
    capture_service, _resource_service, workspace_id = _capture_service(sqlite_connection)
    envelope = _envelope(
        workspace_id,
        payload_kind=CapturePayloadKind.TEXT,
        url=None,
        text="just a note, no url here",
    )

    first = capture_service.submit(envelope)
    second = capture_service.submit(envelope)

    assert second.was_replayed
    assert second.document_id == first.document_id
