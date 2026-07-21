"""Spatial presentation: `Canvas` and `CanvasPlacement`.

A node's identity and data are canonical; a `CanvasPlacement` only records where and how
that node appears on one particular canvas. The same node may have independent
placements on many canvases (CQ-01: multiple canvases, not one infinite workspace).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    CanvasId,
    CanvasPlacementId,
    NodeId,
    WorkspaceId,
    new_id,
)


class Canvas(BaseModel):
    """One named spatial surface within a workspace."""

    id: CanvasId = Field(default_factory=lambda: CanvasId(new_id()))
    workspace_id: WorkspaceId
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("Canvas.name must not be empty")
        return stripped


class CanvasPlacement(BaseModel):
    """Where and how one node is presented on one canvas."""

    id: CanvasPlacementId = Field(default_factory=lambda: CanvasPlacementId(new_id()))
    canvas_id: CanvasId
    node_id: NodeId
    position_x: float
    position_y: float
    width: float = 240.0
    height: float = 120.0
    is_collapsed: bool = False

    @field_validator("width", "height")
    @classmethod
    def _validate_positive(cls, value: float) -> float:
        if value <= 0:
            raise InvariantViolationError("CanvasPlacement width/height must be positive")
        return value
