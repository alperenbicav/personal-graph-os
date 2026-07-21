"""The research library: `Resource` extends a `Node` with lifecycle and provenance.

A `Resource` always backs a canonical `Node`; it adds the fields that make unfinished
research resurface instead of disappearing (progress, next action, review timing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, ResourceId, WorkspaceId, new_id


class ResourceKind(StrEnum):
    PAPER = "paper"
    GITHUB_REPOSITORY = "github_repository"
    DOCUMENTATION = "documentation"
    SPECIFICATION = "specification"
    ARTICLE = "article"
    DATASET = "dataset"
    VIDEO = "video"
    BOOK = "book"
    OTHER = "other"


class ResourceLifecycleStatus(StrEnum):
    INBOX = "inbox"
    TO_REVIEW = "to_review"
    READING = "reading"
    PAUSED = "paused"
    REVIEWED = "reviewed"
    APPLIED = "applied"
    ARCHIVED = "archived"


class Resource(BaseModel):
    """Canonical identity, lifecycle, and resurfacing data for one research item."""

    id: ResourceId = Field(default_factory=lambda: ResourceId(new_id()))
    workspace_id: WorkspaceId
    node_id: NodeId
    kind: ResourceKind
    canonical_identifier: str
    source_url: str | None = None
    lifecycle_status: ResourceLifecycleStatus = ResourceLifecycleStatus.INBOX
    next_action: str | None = None
    # Explicit, persisted acknowledgement that the user chose not to set a next action while
    # pausing. Distinguishes "paused without a plan, on purpose" from a caller that simply
    # forgot to supply one — the pause invariant below accepts either a `next_action` or this
    # flag, never neither.
    next_action_dismissed: bool = False
    open_questions: tuple[str, ...] = ()
    takeaways: tuple[str, ...] = ()
    review_at: datetime | None = None
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("canonical_identifier")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("Resource.canonical_identifier must not be empty")
        return stripped

    def model_post_init(self, _context: object) -> None:
        is_paused_without_a_plan = (
            self.lifecycle_status is ResourceLifecycleStatus.PAUSED
            and self.next_action is None
            and not self.next_action_dismissed
        )
        if is_paused_without_a_plan:
            raise InvariantViolationError(
                f"Resource {self.id} is paused but declares no next_action and no "
                "explicit dismissal; pausing requires a next action or an explicit dismissal"
            )

    def is_due_for_resurfacing(self, *, as_of: datetime, stale_after_days: int) -> bool:
        """Whether this resource should reappear in Continue Reading / Stale Resources.

        Applies both `review_at` and an inactivity threshold so unfinished research does
        not silently disappear even when no explicit review date was set.
        """
        if self.lifecycle_status in (
            ResourceLifecycleStatus.APPLIED,
            ResourceLifecycleStatus.ARCHIVED,
        ):
            return False
        if self.review_at is not None and self.review_at <= as_of:
            return True
        inactive_seconds = (as_of - self.last_activity_at).total_seconds()
        return inactive_seconds >= stale_after_days * 86400
