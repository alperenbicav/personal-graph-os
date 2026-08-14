"""Channel-neutral capture intake contract (EP-2026-012 ST-02).

`CaptureEnvelope` is the one typed shape every capture channel (manual call, future ClickUp/
Telegram adapters) normalizes its input into before `CaptureService` touches it: a URL, file,
free text, or external item, tagged with the caller's intent. Extraction adapters (ST-03) and
agentic classification/relation proposals (ST-04) are separate stories -- this contract only
decides *what kind of thing* was captured and *what the caller wants done with it*, never how to
actually enrich or plan it.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, model_validator

from personal_graph_os.domain.errors import DomainError, InvariantViolationError
from personal_graph_os.domain.identifiers import FileReferenceId, WorkspaceId


class CaptureIdempotencyConflictError(DomainError):
    """Raised when `(workspace_id, source, request_id)` repeats but the retried envelope's
    normalized command content no longer matches the one that key originally committed
    (review finding S2-R01 delta): a changed URL, intent, or operation set is a genuine
    conflict, never a safe replay -- reusing the prior committed entity for different
    instructions could enrich or plan the wrong object."""


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class CaptureIntent(StrEnum):
    """What the caller wants done with the captured content."""

    SAVE_RAW = "save_raw"
    ENRICH = "enrich"
    PLAN_WORK = "plan_work"


class CapturePayloadKind(StrEnum):
    URL = "url"
    FILE = "file"
    TEXT = "text"
    EXTERNAL_ITEM = "external_item"


class CaptureOperationKind(StrEnum):
    """The fixed, bounded vocabulary `capture_parsing` recognizes in explicit message text.

    Deliberately small: an operation this contract does not name is never guessed at, only
    reported back as an unrecognized segment requiring clarification.
    """

    SUMMARIZE = "summarize"
    EXTRACT_KEY_FINDINGS = "extract_key_findings"
    RELATE_TO = "relate_to"
    PLAN = "plan"


class CaptureOperation(BaseModel):
    """One bounded, explicit operation -- either declared directly by a structured caller
    (e.g. an MCP agent) or parsed from free text by `capture_parsing.parse_capture_text`.

    Never executed by `CaptureService` itself: recorded on the resulting `CaptureOutcome` for a
    later story's enrichment/relation/work-planning orchestrator (ST-03/04/05) to pick up.
    """

    kind: CaptureOperationKind
    target: str | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> CaptureOperation:
        if self.kind is CaptureOperationKind.RELATE_TO and not (self.target or "").strip():
            raise InvariantViolationError(
                "CaptureOperation.target is required when kind is relate_to"
            )
        if self.kind is not CaptureOperationKind.RELATE_TO and self.target is not None:
            raise InvariantViolationError(
                f"CaptureOperation.target is only meaningful for relate_to, not {self.kind}"
            )
        return self


class CaptureEnvelope(BaseModel):
    """One normalized capture request, whatever channel it arrived on.

    Exactly one of `url` / `file_reference_id` / `text` / `external_item_id` is populated,
    matching `payload_kind` -- a caller declares what it captured, not a free-form bag of
    optional fields. `(workspace_id, source, request_id)` is the stable idempotent replay key
    `CaptureService` checks before doing any work, mirroring `IngestionJob`'s own identity.
    """

    workspace_id: WorkspaceId
    source: str
    request_id: str
    actor_name: str
    payload_kind: CapturePayloadKind
    url: str | None = None
    file_reference_id: FileReferenceId | None = None
    external_item_id: str | None = None
    text: str | None = None
    intent: CaptureIntent = CaptureIntent.SAVE_RAW
    # Explicit, structurally-declared operations -- e.g. an MCP agent that already knows it wants
    # a summary. Free-text channels never populate this directly; `CaptureService` derives
    # operations for `TEXT` payloads from `text` via `capture_parsing` instead.
    operations: tuple[CaptureOperation, ...] = ()
    title: str | None = None
    # Channel metadata (ST-10), like `title`: the canonical external link of the captured item
    # (e.g. the imported ClickUp task's deep link). Stored on the resulting `IngestionJob` as
    # provenance and included in the idempotency fingerprint, so a changed external link on a
    # reused request id is a genuine conflict, never a silent replay.
    external_url: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> CaptureEnvelope:
        for field_label, value in (
            ("CaptureEnvelope.source", self.source),
            ("CaptureEnvelope.request_id", self.request_id),
            ("CaptureEnvelope.actor_name", self.actor_name),
        ):
            _non_empty(value, field_label)
        for field_label, value in (
            ("CaptureEnvelope.url", self.url),
            ("CaptureEnvelope.external_item_id", self.external_item_id),
            ("CaptureEnvelope.text", self.text),
            ("CaptureEnvelope.title", self.title),
            ("CaptureEnvelope.external_url", self.external_url),
        ):
            if value is not None:
                _non_empty(value, field_label)

        payload_fields = {
            CapturePayloadKind.URL: ("url",),
            CapturePayloadKind.FILE: ("file_reference_id",),
            CapturePayloadKind.TEXT: ("text",),
            CapturePayloadKind.EXTERNAL_ITEM: ("external_item_id",),
        }
        required_field = payload_fields[self.payload_kind][0]
        other_fields = ("url", "file_reference_id", "text", "external_item_id")
        if getattr(self, required_field) is None:
            raise InvariantViolationError(
                f"CaptureEnvelope.{required_field} is required when payload_kind is "
                f"{self.payload_kind}"
            )
        for other_field in other_fields:
            if other_field == required_field:
                continue
            # `text` may accompany a `url` payload as an evidence/instruction caption (the
            # Telegram-style "<url> summarize, ..." message) or an `external_item` payload as
            # the item's own verbatim body (ST-10: a ClickUp task's name+description, stored
            # unchanged -- never re-parsed as a channel message). Every other combination is
            # rejected so a caller cannot declare two payloads at once.
            if other_field == "text" and self.payload_kind in (
                CapturePayloadKind.URL,
                CapturePayloadKind.EXTERNAL_ITEM,
            ):
                continue
            if getattr(self, other_field) is not None:
                raise InvariantViolationError(
                    f"CaptureEnvelope.{other_field} must not be set when payload_kind is "
                    f"{self.payload_kind}"
                )

        if self.intent is CaptureIntent.SAVE_RAW and self.operations:
            raise InvariantViolationError(
                "CaptureEnvelope.operations must be empty when intent is save_raw: "
                "save_raw never infers enrichment or planning work"
            )
        if self.payload_kind is CapturePayloadKind.TEXT and self.operations:
            raise InvariantViolationError(
                "CaptureEnvelope.operations must not be declared for a text payload: "
                "operations are derived from parsing CaptureEnvelope.text instead"
            )
        return self

    def content_fingerprint(self) -> str:
        """A stable content hash of every field `CaptureService._interpret()` reads, mirroring
        `AgentGatewayService`'s `payload_fingerprint` convention (`infrastructure/mcp/gateway.py`).

        Binds `(workspace_id, source, request_id)` to the exact command that produced its
        committed job: a retried envelope whose fingerprint no longer matches is a genuine
        idempotency conflict, not a safe replay.
        """
        canonical = {
            "payload_kind": self.payload_kind.value,
            "url": self.url,
            "file_reference_id": self.file_reference_id,
            "external_item_id": self.external_item_id,
            "text": self.text,
            "intent": self.intent.value,
            "operations": [
                {"kind": operation.kind.value, "target": operation.target}
                for operation in self.operations
            ],
            "title": self.title,
            "external_url": self.external_url,
        }
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
