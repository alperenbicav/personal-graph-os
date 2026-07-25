"""The `plan_work` work planner's typed classifier output (EP-2026-012 ST-05).

A `WorkPlanningProvider` (`application.work_planning_adapters`) decides whether a captured
piece of text actually justifies an Epic -> Story -> Task hierarchy at all -- `plan_work` must
never create one unconditionally. When it does not, `WorkPlanResult.is_justified` is `False` and
every hierarchy/plan field stays empty; the caller's raw capture (already committed by
`CaptureService`, ST-02) is the only durable result. When it does, the result also carries the
executor-neutral plan's own Markdown body -- the versioned Wiki Document `WorkPlanningService`
persists alongside the hierarchy.

Every field is bounded (mirroring `domain.enrichment`'s review finding S4-R06 lesson): a faulty
or adversarial provider must never be able to amplify bounded source input into an unbounded
hierarchy or an oversized plan document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from personal_graph_os.domain.errors import DomainError, InvariantViolationError
from personal_graph_os.domain.identifiers import (
    DocumentId,
    DocumentVersionId,
    IngestionJobId,
    WorkItemId,
    WorkPlanningReceiptId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.work_items import WorkItemType

MAX_TITLE_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 4_000
MAX_REASON_LENGTH = 500
MAX_PLAN_BODY_LENGTH = 20_000
MAX_STORIES = 20
MAX_TASKS_PER_STORY = 20


class WorkPlanningError(DomainError):
    """Base error for a `plan_work` request the planner could not resolve."""


class WorkPlanningIdempotencyConflictError(WorkPlanningError):
    """Raised when a `plan_from_capture` call names an `ingestion_job_id` that a *different*
    `WorkPlanningReceipt` already claims (review finding S5-R04): each captured job may produce
    at most one plan outcome, so this is a genuine identity conflict, never a safe replay."""


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


def _bounded(value: str, max_length: int, field_label: str) -> str:
    if len(value) > max_length:
        raise InvariantViolationError(
            f"{field_label} must not exceed {max_length} characters, got {len(value)}"
        )
    return value


class ProposedTask(BaseModel):
    """One candidate Task under a `ProposedStory`."""

    title: str
    work_type: WorkItemType
    description: str = ""

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _bounded(
            _non_empty(value, "ProposedTask.title"), MAX_TITLE_LENGTH, "ProposedTask.title"
        )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _bounded(value, MAX_DESCRIPTION_LENGTH, "ProposedTask.description")


class ProposedStory(BaseModel):
    """One candidate Story under the plan's single `ProposedEpic`, with at least one task."""

    title: str
    work_type: WorkItemType
    description: str = ""
    tasks: tuple[ProposedTask, ...]

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _bounded(
            _non_empty(value, "ProposedStory.title"), MAX_TITLE_LENGTH, "ProposedStory.title"
        )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _bounded(value, MAX_DESCRIPTION_LENGTH, "ProposedStory.description")

    @field_validator("tasks")
    @classmethod
    def _validate_tasks(cls, value: tuple[ProposedTask, ...]) -> tuple[ProposedTask, ...]:
        if not value:
            raise InvariantViolationError("ProposedStory.tasks must include at least one task")
        if len(value) > MAX_TASKS_PER_STORY:
            raise InvariantViolationError(
                f"ProposedStory.tasks must not exceed {MAX_TASKS_PER_STORY} entries, "
                f"got {len(value)}"
            )
        return value


class ProposedEpic(BaseModel):
    """The plan's single hierarchy root."""

    title: str
    work_type: WorkItemType
    description: str = ""

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _bounded(
            _non_empty(value, "ProposedEpic.title"), MAX_TITLE_LENGTH, "ProposedEpic.title"
        )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _bounded(value, MAX_DESCRIPTION_LENGTH, "ProposedEpic.description")


class WorkPlanResult(BaseModel):
    """The bounded, typed shape every `WorkPlanningProvider` returns.

    When `is_justified` is `False`, `epic`/`stories`/`plan_title`/`plan_body_markdown` must all
    be unset -- `plan_work` never partially creates a hierarchy. When it is `True`, all four are
    required and `stories` must be non-empty, so `WorkPlanningService` never has to guess a
    default hierarchy shape for an incomplete "yes" answer.
    """

    is_justified: bool
    reason: str
    epic: ProposedEpic | None = None
    stories: tuple[ProposedStory, ...] = ()
    plan_title: str | None = None
    plan_body_markdown: str | None = None

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return _bounded(
            _non_empty(value, "WorkPlanResult.reason"), MAX_REASON_LENGTH, "WorkPlanResult.reason"
        )

    @field_validator("stories")
    @classmethod
    def _validate_stories(cls, value: tuple[ProposedStory, ...]) -> tuple[ProposedStory, ...]:
        if len(value) > MAX_STORIES:
            raise InvariantViolationError(
                f"WorkPlanResult.stories must not exceed {MAX_STORIES} entries, got {len(value)}"
            )
        return value

    @field_validator("plan_title")
    @classmethod
    def _validate_plan_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded(
            _non_empty(value, "WorkPlanResult.plan_title"),
            MAX_TITLE_LENGTH,
            "WorkPlanResult.plan_title",
        )

    @field_validator("plan_body_markdown")
    @classmethod
    def _validate_plan_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded(
            _non_empty(value, "WorkPlanResult.plan_body_markdown"),
            MAX_PLAN_BODY_LENGTH,
            "WorkPlanResult.plan_body_markdown",
        )

    @model_validator(mode="after")
    def _validate_justified_shape(self) -> WorkPlanResult:
        required_when_justified = (
            self.epic is not None,
            bool(self.stories),
            self.plan_title is not None,
            self.plan_body_markdown is not None,
        )
        if self.is_justified and not all(required_when_justified):
            raise InvariantViolationError(
                "WorkPlanResult.is_justified requires epic, at least one story, plan_title, "
                "and plan_body_markdown to all be set"
            )
        if not self.is_justified and any(required_when_justified):
            raise InvariantViolationError(
                "WorkPlanResult must not carry epic/stories/plan_title/plan_body_markdown "
                "when is_justified is False"
            )
        return self


class WorkPlanOutcomeStatus(StrEnum):
    """The terminal decision a `plan_from_capture` call reached for one `IngestionJob` (review
    finding S5-R04): a classifier's "do not plan" answer is just as terminal as a hierarchy it
    actually created, so both must be receipted -- otherwise a retried job keeps re-invoking the
    provider forever and a later nondeterministic/model-changed retry could plan a capture whose
    first decision was "do not plan"."""

    PLANNED = "planned"
    NOT_JUSTIFIED = "not_justified"


class WorkPlanningReceipt(BaseModel):
    """The durable proof that one `IngestionJob` already reached a terminal `plan_work` decision
    (review finding S5-R04): unlike `IdempotencyReceipt` (unique per `(workspace_id, source,
    actor_name, request_id)`, already claimed by `CaptureService`'s own receipt for the same
    request), this is unique per `ingestion_job_id` -- the identity a capture replay always
    resolves to the same value for, regardless of how many times the request is retried. A
    second `plan_from_capture` call for a job that already has one replays the original decision
    -- the reconstructed hierarchy/document identifiers when `status` is `PLANNED`, or `None`
    when it is `NOT_JUSTIFIED` -- instead of invoking the provider again."""

    id: WorkPlanningReceiptId = Field(default_factory=lambda: WorkPlanningReceiptId(new_id()))
    workspace_id: WorkspaceId
    ingestion_job_id: IngestionJobId
    status: WorkPlanOutcomeStatus
    epic_work_item_id: WorkItemId | None = None
    plan_document_id: DocumentId | None = None
    plan_document_version_id: DocumentVersionId | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _validate_status_shape(self) -> WorkPlanningReceipt:
        plan_fields_set = (
            self.epic_work_item_id is not None,
            self.plan_document_id is not None,
            self.plan_document_version_id is not None,
        )
        if self.status is WorkPlanOutcomeStatus.PLANNED and not all(plan_fields_set):
            raise InvariantViolationError(
                "WorkPlanningReceipt.status PLANNED requires epic_work_item_id, "
                "plan_document_id, and plan_document_version_id to all be set"
            )
        if self.status is WorkPlanOutcomeStatus.NOT_JUSTIFIED and any(plan_fields_set):
            raise InvariantViolationError(
                "WorkPlanningReceipt.status NOT_JUSTIFIED must not carry epic_work_item_id, "
                "plan_document_id, or plan_document_version_id"
            )
        return self
