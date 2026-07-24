"""The canonical Epic -> Story -> Task work hierarchy.

A `WorkItem` extends a `Node` the same way `Resource` does (EP-2026-012 selected approach): the
node carries title/body/graph projection, `WorkItem` carries the work-specific hierarchy,
status, repository association, and provenance. Whether a hierarchy is warranted for a given
piece of work at all is a `plan_work` policy decision made by a later story, not a structural
rule enforced here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, WorkItemId, WorkspaceId, new_id


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
    # Capture provenance: "manual", "agent:<identity>", "clickup", "telegram", etc.
    source: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _non_empty(value, "WorkItem.source")

    def model_post_init(self, _context: object) -> None:
        if self.kind is WorkItemKind.EPIC and self.parent_id is not None:
            raise InvariantViolationError(
                f"WorkItem {self.id} is an epic and must not declare a parent_id"
            )
        if self.parent_id is not None and self.parent_id == self.id:
            raise InvariantViolationError(f"WorkItem {self.id} cannot be its own parent")
