"""Per-channel sync cursor for external ingress adapters (EP-2026-012 ST-10/11).

`ChannelSyncState` records one monotonic cursor per `(workspace_id, channel)` so a channel
adapter can resume a later incremental/bidirectional sync from where the last successful import
stopped. Today `clickup` and `telegram` both write a cursor. The cursor is purely an ingress
position -- it never authorizes a write back to the channel.

**Cursor contract:** each channel writes a fixed-width, lexicographically-sortable cursor string,
because the shared upsert keeps the cursor monotonic with a text `MAX(cursor_value,
excluded.cursor_value)` (S10-F02/S11-F01). ClickUp stores ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS+00:00`,
fixed-width within its format); Telegram stores the `update_id` zero-padded to
`TELEGRAM_CURSOR_WIDTH` digits so numeric and lexicographic order agree at every digit boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import WorkspaceId


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class ChannelSyncState(BaseModel):
    workspace_id: WorkspaceId
    channel: str
    cursor_value: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("channel", "cursor_value")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "ChannelSyncState field")
        return _non_empty(value, f"ChannelSyncState.{field_name}")
