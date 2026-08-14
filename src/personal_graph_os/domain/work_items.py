"""The canonical Epic -> Story -> Task work hierarchy.

A `WorkItem` extends a `Node` the same way `Resource` does (EP-2026-012 selected approach): the
node carries title/body/graph projection, `WorkItem` carries the work-specific hierarchy,
status, repository association, and provenance. Whether a hierarchy is warranted for a given
piece of work at all is a `plan_work` policy decision made by a later story, not a structural
rule enforced here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, StrictInt, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    NodeId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
    new_id,
)


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class WorkItemKind(StrEnum):
    EPIC = "epic"
    STORY = "story"
    TASK = "task"


class WorkItemType(StrEnum):
    """What kind of work this is, orthogonal to `WorkItemKind`'s hierarchy level (an Epic,
    Story, or Task can each be any of these): the approved Tasks design contract's editable
    Type selector, distinct from the Status selector."""

    FEATURE = "feature"
    FIX = "fix"
    REFACTOR = "refactor"
    RESEARCH = "research"
    OPS = "ops"
    DOCS = "docs"


class WorkItemStatus(StrEnum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    PRODUCTION = "production"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class WorkItemPriority(StrEnum):
    """The Tasks design contract's editable Priority selector (ST-08)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkItem(BaseModel):
    """Canonical identity, hierarchy, and lifecycle for one Epic, Story, or Task."""

    id: WorkItemId = Field(default_factory=lambda: WorkItemId(new_id()))
    workspace_id: WorkspaceId
    node_id: NodeId
    kind: WorkItemKind
    work_type: WorkItemType
    status: WorkItemStatus = WorkItemStatus.BACKLOG
    # An Epic's own hierarchy root, so `parent_id` is always `None` for it; a Story/Task may
    # optionally declare one, though which parent kinds are actually legal is enforced by the
    # service layer that can look up the referenced item's kind, not by this model alone.
    parent_id: WorkItemId | None = None
    # The repository's own stable projection `Node` id (never its display name, which can be
    # renamed or duplicated): review finding R01 -- a free-text repository name silently
    # detaches or misassociates work across a rename, while a node id does not.
    repository_node_id: NodeId | None = None
    # ST-08 planning fields. `assignee` ("actor") is free text distinct from `source` (who is
    # responsible now, not where the item came from); `blockers` is free text describing what
    # blocks it; `due_date` is a single calendar date ("dates" = one due date); `progress_percent`
    # mirrors `Resource.progress_percent` (0-100, None means unrecorded, not 0).
    priority: WorkItemPriority | None = None
    due_date: date | None = None
    assignee: str | None = None
    blockers: str | None = None
    progress_percent: StrictInt | None = None
    # Soft-archive flag (ST-09): archive is the default, recoverable lifecycle for a work item,
    # independent of its backing Node (which stays visible on the graph). `None`/`False` means
    # active; restore clears it; a hard delete removes the row and its backing node together.
    is_archived: bool = False
    # Capture provenance: "manual", "agent:<identity>", "clickup", "telegram", etc.
    source: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _non_empty(value, "WorkItem.source")

    @field_validator("assignee", "blockers")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_empty(value, "WorkItem.free_text")

    @field_validator("progress_percent")
    @classmethod
    def _validate_progress_percent(cls, value: StrictInt | None) -> StrictInt | None:
        if value is not None and not (0 <= value <= 100):
            raise InvariantViolationError(
                f"WorkItem.progress_percent must be between 0 and 100, got {value}"
            )
        return value

    def model_post_init(self, _context: object) -> None:
        if self.kind is WorkItemKind.EPIC and self.parent_id is not None:
            raise InvariantViolationError(
                f"WorkItem {self.id} is an epic and must not declare a parent_id"
            )
        if self.parent_id is not None and self.parent_id == self.id:
            raise InvariantViolationError(f"WorkItem {self.id} cannot be its own parent")


class WorkItemChecklistItem(BaseModel):
    """One checkbox row inside a work item's checklist (ST-08).

    A stable row with its own id rather than an embedded JSON array, so toggle/reorder/remove
    are idempotent and auditable. `position` is a dense 0-based order maintained by the service.
    """

    id: WorkItemChecklistItemId = Field(default_factory=lambda: WorkItemChecklistItemId(new_id()))
    work_item_id: WorkItemId
    position: int
    label: str
    is_completed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return _non_empty(value, "WorkItemChecklistItem.label")

    @field_validator("position")
    @classmethod
    def _validate_position(cls, value: int) -> int:
        if value < 0:
            raise InvariantViolationError(
                f"WorkItemChecklistItem.position must be non-negative, got {value}"
            )
        return value
