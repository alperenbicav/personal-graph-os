from __future__ import annotations

import pytest

from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIntent,
    CaptureOperation,
    CaptureOperationKind,
    CapturePayloadKind,
)
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import FileReferenceId, WorkspaceId, new_id


def _envelope(
    payload_kind: CapturePayloadKind,
    *,
    source: str = "manual",
    url: str | None = None,
    file_reference_id: FileReferenceId | None = None,
    external_item_id: str | None = None,
    text: str | None = None,
    intent: CaptureIntent = CaptureIntent.SAVE_RAW,
    operations: tuple[CaptureOperation, ...] = (),
) -> CaptureEnvelope:
    return CaptureEnvelope(
        workspace_id=WorkspaceId(new_id()),
        source=source,
        request_id="req-1",
        actor_name="alperen",
        payload_kind=payload_kind,
        url=url,
        file_reference_id=file_reference_id,
        external_item_id=external_item_id,
        text=text,
        intent=intent,
        operations=operations,
    )


def test_url_envelope_requires_url() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(CapturePayloadKind.URL)

    envelope = _envelope(CapturePayloadKind.URL, url="https://example.com/article")
    assert envelope.url == "https://example.com/article"


def test_url_envelope_may_carry_accompanying_text() -> None:
    envelope = _envelope(
        CapturePayloadKind.URL, url="https://example.com/article", text="summarize"
    )
    assert envelope.text == "summarize"


def test_file_envelope_rejects_a_url_field() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(
            CapturePayloadKind.FILE,
            file_reference_id=FileReferenceId(new_id()),
            url="https://example.com",
        )


def test_text_envelope_requires_text() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(CapturePayloadKind.TEXT)


def test_text_envelope_rejects_declared_operations() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(
            CapturePayloadKind.TEXT,
            text="https://example.com summarize",
            intent=CaptureIntent.ENRICH,
            operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
        )


def test_external_item_envelope_requires_external_item_id() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(CapturePayloadKind.EXTERNAL_ITEM)


def test_save_raw_intent_rejects_declared_operations() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(
            CapturePayloadKind.URL,
            url="https://example.com/article",
            intent=CaptureIntent.SAVE_RAW,
            operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
        )


def test_enrich_intent_accepts_declared_operations() -> None:
    envelope = _envelope(
        CapturePayloadKind.URL,
        url="https://example.com/article",
        intent=CaptureIntent.ENRICH,
        operations=(CaptureOperation(kind=CaptureOperationKind.SUMMARIZE),),
    )
    assert envelope.operations[0].kind is CaptureOperationKind.SUMMARIZE


def test_blank_source_is_rejected() -> None:
    with pytest.raises(InvariantViolationError):
        _envelope(CapturePayloadKind.URL, url="https://example.com/article", source="   ")


def test_relate_to_operation_requires_a_target() -> None:
    with pytest.raises(InvariantViolationError):
        CaptureOperation(kind=CaptureOperationKind.RELATE_TO)


def test_non_relate_to_operation_rejects_a_target() -> None:
    with pytest.raises(InvariantViolationError):
        CaptureOperation(kind=CaptureOperationKind.SUMMARIZE, target="something")
