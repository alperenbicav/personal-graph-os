"""Resumable capture pipeline jobs: `IngestionJob` tracks one capture through deterministic
stages so a crash or retry resumes instead of silently re-running or losing work.

`received -> normalized -> extracted -> enriched -> linked -> committed` is the fixed order
(EP-2026-012 selected approach); a job advances exactly one checkpoint at a time -- never
skipped, repeated, or rewound -- and its `(workspace_id, source, source_identifier)` triple is
the idempotent replay key later channel/capture adapters key off of.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import IngestionJobId, WorkspaceId, new_id


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class IngestionStage(StrEnum):
    RECEIVED = "received"
    NORMALIZED = "normalized"
    EXTRACTED = "extracted"
    ENRICHED = "enriched"
    LINKED = "linked"
    COMMITTED = "committed"


_STAGE_ORDER: tuple[IngestionStage, ...] = (
    IngestionStage.RECEIVED,
    IngestionStage.NORMALIZED,
    IngestionStage.EXTRACTED,
    IngestionStage.ENRICHED,
    IngestionStage.LINKED,
    IngestionStage.COMMITTED,
)


class IngestionJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IngestionJob(BaseModel):
    """One resumable capture, from raw intake through a committed canonical write."""

    id: IngestionJobId = Field(default_factory=lambda: IngestionJobId(new_id()))
    workspace_id: WorkspaceId
    # Capture channel/adapter identity: "manual", "agent:<identity>", "clickup", "telegram".
    source: str
    # Stable identity for idempotent replay -- e.g. a URL hash, ClickUp item id, or Telegram
    # `update_id`. Unique per `(workspace_id, source, source_identifier)`.
    source_identifier: str
    stage: IngestionStage = IngestionStage.RECEIVED
    status: IngestionJobStatus = IngestionJobStatus.PENDING
    result_entity_type: str | None = None
    result_entity_id: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source", "source_identifier")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "IngestionJob field")
        return _non_empty(value, f"IngestionJob.{field_name}")

    def model_post_init(self, _context: object) -> None:
        # Review finding R03: `committed` is a terminal stage that means exactly one thing --
        # the job succeeded and produced a result. A job that failed or is still pending/running
        # must stay recorded at whichever earlier stage it was actually attempting, not be
        # advanced to `committed` in a non-success status.
        if (
            self.stage is IngestionStage.COMMITTED
            and self.status is not IngestionJobStatus.SUCCEEDED
        ):
            raise InvariantViolationError(
                f"IngestionJob {self.id} is at stage committed but status is {self.status}: "
                "committed only ever means succeeded"
            )
        if (
            self.status is IngestionJobStatus.SUCCEEDED
            and self.stage is not IngestionStage.COMMITTED
        ):
            raise InvariantViolationError(
                f"IngestionJob {self.id} is succeeded but stage is {self.stage}, not committed: "
                "success is only legal once every stage has been recorded"
            )
        is_committed_without_result = self.stage is IngestionStage.COMMITTED and (
            self.result_entity_type is None or self.result_entity_id is None
        )
        if is_committed_without_result:
            raise InvariantViolationError(
                f"IngestionJob {self.id} is committed but declares no "
                "result_entity_type/result_entity_id"
            )
        if self.status is IngestionJobStatus.FAILED and self.error_message is None:
            raise InvariantViolationError(
                f"IngestionJob {self.id} is failed but declares no error_message"
            )

    def advance_to(
        self,
        stage: IngestionStage,
        *,
        result_entity_type: str | None = None,
        result_entity_id: str | None = None,
    ) -> IngestionJob:
        """Return a copy moved forward to `stage`, exactly one checkpoint at a time.

        A stage whose work turned out to be a no-op (e.g. no enrichment was needed) is still
        recorded by advancing through it explicitly -- `stage` must be the checkpoint
        immediately after the job's current one, never a skip-ahead, a repeat, or backward
        (review finding R03). Advancing to `committed` requires both `result_entity_type` and
        `result_entity_id`. A `FAILED` job cannot advance at all -- call `retry()` first, which
        re-attempts the *current* stage rather than moving past it.
        """
        if self.status is IngestionJobStatus.FAILED:
            raise InvariantViolationError(
                f"IngestionJob {self.id} is failed at stage {self.stage}: call retry() before "
                "advancing, a failed job cannot move to the next stage"
            )
        current_index = _STAGE_ORDER.index(self.stage)
        next_index = _STAGE_ORDER.index(stage)
        if next_index != current_index + 1:
            raise InvariantViolationError(
                f"IngestionJob {self.id} cannot advance from {self.stage} to {stage}: "
                "stages move forward exactly one checkpoint at a time"
            )
        update: dict[str, object] = {"stage": stage, "updated_at": datetime.now(UTC)}
        if stage is IngestionStage.COMMITTED:
            if result_entity_type is None or result_entity_id is None:
                raise InvariantViolationError(
                    f"IngestionJob {self.id} cannot advance to committed without both "
                    "result_entity_type and result_entity_id"
                )
            update["status"] = IngestionJobStatus.SUCCEEDED
            update["result_entity_type"] = result_entity_type
            update["result_entity_id"] = result_entity_id
        else:
            update["status"] = IngestionJobStatus.PENDING
        return self.model_copy(update=update)

    def retry(self) -> IngestionJob:
        """Return a copy that re-attempts the job's *current* stage: clears `error_message` and
        enters `pending`, ready to be worked again -- the stage itself never rewinds.

        Only legal from `FAILED` (review finding R03's remaining delta): retrying a job that is
        not failed would silently discard a `running`/`succeeded` outcome, so this raises instead.
        """
        if self.status is not IngestionJobStatus.FAILED:
            raise InvariantViolationError(
                f"IngestionJob {self.id} cannot retry from status {self.status}: only a failed "
                "job may be retried"
            )
        return self.model_copy(
            update={
                "status": IngestionJobStatus.PENDING,
                "error_message": None,
                "updated_at": datetime.now(UTC),
            }
        )
