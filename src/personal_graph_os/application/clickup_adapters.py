"""ClickUp channel-adapter contract: the only boundary `ClickupService` depends on
(EP-2026-012 ST-10), mirroring `application/work_planning_adapters.py` and
`application/extraction_adapters.py`.

A concrete real client and an offline fake both live in `infrastructure/clickup/`; application
code never imports them. `ClickUpTask` is the typed, channel-normalized read model the adapter
returns -- id, name, description, canonical deep link, and `date_updated` (the sync cursor) --
already freed of ClickUp's epoch-milliseconds wire format.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import DomainError


class ClickUpError(DomainError):
    """Base type for every ClickUp channel failure surfaced to the application layer."""


class ClickUpNotConfiguredError(ClickUpError):
    """Raised when a ClickUp import is attempted but no `ClickUpClient` is wired
    (`PGOS_CLICKUP_API_TOKEN` unset): fails closed, never silently runs unconfigured."""


class ClickUpFetchFailedError(ClickUpError):
    """Raised when the ClickUp API could not be reached or returned an unusable response
    (network/timeout, non-2xx other than the mapped 401/403/404 cases, malformed JSON)."""


class ClickUpAccessDeniedError(ClickUpError):
    """Raised when the ClickUp API rejects the configured token (401/403)."""


class ClickUpTaskNotFoundError(ClickUpError):
    """Raised when the requested task id does not exist (ClickUp returns 404, which is
    indistinguishable from a task the token cannot see)."""


class ClickUpTask(BaseModel):
    id: str
    name: str
    description: str = ""
    url: str | None = None
    date_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("id", "name")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        stripped = value.strip()
        if not stripped:
            field_name = getattr(info, "field_name", "ClickUpTask field")
            raise ValueError(f"{field_name} must not be empty")
        return stripped


class ClickUpClient(Protocol):
    """Reads one ClickUp task by its stable task id.

    Raises `ClickUpTaskNotFoundError` for an unknown/invisible task,
    `ClickUpAccessDeniedError` for an invalid token, and `ClickUpFetchFailedError` for any
    transport/unusable-response failure. Never writes to ClickUp.
    """

    def get_task(self, task_id: str) -> ClickUpTask: ...


__all__ = [
    "ClickUpAccessDeniedError",
    "ClickUpClient",
    "ClickUpError",
    "ClickUpFetchFailedError",
    "ClickUpNotConfiguredError",
    "ClickUpTask",
    "ClickUpTaskNotFoundError",
]
