"""Projections over the canonical graph: `SavedView` and `ContextPack`.

Neither holds canonical node/edge state; both describe how to select and present it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    ContextPackId,
    EdgeId,
    NodeId,
    SavedViewId,
    WorkspaceId,
    new_id,
)


class ViewKind(StrEnum):
    """The structured projections a `SavedView` may render as."""

    TABLE = "table"
    KANBAN = "kanban"
    TIMELINE = "timeline"
    CANVAS = "canvas"
    SEARCH = "search"
    ACTIVITY = "activity"


class SavedView(BaseModel):
    """A named, reusable filter/sort projection of the canonical graph."""

    id: SavedViewId = Field(default_factory=lambda: SavedViewId(new_id()))
    workspace_id: WorkspaceId
    name: str
    view_kind: ViewKind
    filter_definition: dict[str, object] = Field(default_factory=dict)
    sort_definition: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("SavedView.name must not be empty")
        return stripped


class ContextPack(BaseModel):
    """A reproducible, bounded selection of nodes/edges/evidence for agent handoff."""

    id: ContextPackId = Field(default_factory=lambda: ContextPackId(new_id()))
    workspace_id: WorkspaceId
    name: str
    node_ids: tuple[NodeId, ...] = ()
    edge_ids: tuple[EdgeId, ...] = ()
    evidence_pointers: tuple[str, ...] = ()
    inclusion_reasons: dict[str, str] = Field(default_factory=dict)
    object_limit: int = 200
    token_limit: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("ContextPack.name must not be empty")
        return stripped

    @field_validator("object_limit")
    @classmethod
    def _validate_object_limit(cls, value: int) -> int:
        if value <= 0:
            raise InvariantViolationError("ContextPack.object_limit must be positive")
        return value

    def model_post_init(self, _context: object) -> None:
        total_objects = len(self.node_ids) + len(self.edge_ids)
        if total_objects > self.object_limit:
            raise InvariantViolationError(
                f"ContextPack '{self.name}' selects {total_objects} objects, "
                f"exceeding object_limit={self.object_limit}"
            )
