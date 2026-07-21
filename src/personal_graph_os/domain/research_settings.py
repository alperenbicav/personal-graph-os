"""Per-workspace research behavior settings (resurfacing thresholds today)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import WorkspaceId

_MAX_STALE_AFTER_DAYS = 3650


class WorkspaceResearchSettings(BaseModel):
    """Configures when unfinished research resurfaces (product contract default: 14 days)."""

    workspace_id: WorkspaceId
    stale_after_days: int = 14

    @field_validator("stale_after_days")
    @classmethod
    def _validate_stale_after_days(cls, value: int) -> int:
        if not (0 < value <= _MAX_STALE_AFTER_DAYS):
            raise InvariantViolationError(
                f"stale_after_days must be between 1 and {_MAX_STALE_AFTER_DAYS}, got {value}"
            )
        return value
