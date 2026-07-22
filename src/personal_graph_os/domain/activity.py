"""Audit trail: `ActivityEvent` and `DiscoveryRun`.

Every human or agent mutation is recorded with actor, source, and reason so it remains
attributable, auditable, and reversible where the inverse is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    ActivityEventId,
    DiscoveryRunId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.resource import ResourceKind


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class MutationAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    ARCHIVED = "archived"
    RESTORED = "restored"
    DELETED = "deleted"


class ActivityEvent(BaseModel):
    """One recorded human/agent mutation, with enough state to support undo."""

    id: ActivityEventId = Field(default_factory=lambda: ActivityEventId(new_id()))
    workspace_id: WorkspaceId
    actor_kind: ActorKind
    actor_name: str
    source: str
    entity_type: str
    entity_id: str
    action: MutationAction
    session_id: str | None = None
    reason: str | None = None
    before_state: dict[str, object] | None = None
    after_state: dict[str, object] | None = None
    is_undoable: bool = False
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("actor_name", "source", "entity_type", "entity_id")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "ActivityEvent field")
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError(f"ActivityEvent.{field_name} must not be empty")
        return stripped


class DiscoveryOutcome(StrEnum):
    """What ultimately happened to one candidate during `DiscoveryService.apply()`."""

    IMPORTED = "imported"
    REUSED = "reused"
    SKIPPED = "skipped"
    FAILED = "failed"


class DiscoveredCandidate(BaseModel):
    """One candidate a `DiscoveryRun` considered, with the evidence it was submitted with and
    what ultimately happened to it — the durable provenance record ST-06 attributes imports to."""

    raw_identifier: str
    canonical_identifier: str | None = None
    title: str
    kind: ResourceKind | None = None
    description: str = ""
    evidence: tuple[str, ...] = ()
    outcome: DiscoveryOutcome
    reason: str | None = None
    existing_resource_id: str | None = None
    imported_node_id: str | None = None

    @property
    def was_imported(self) -> bool:
        return self.outcome is DiscoveryOutcome.IMPORTED


class DiscoveryRun(BaseModel):
    """A record of one agent-driven natural-language resource discovery/import batch."""

    id: DiscoveryRunId = Field(default_factory=lambda: DiscoveryRunId(new_id()))
    workspace_id: WorkspaceId
    agent_identity: str
    instruction: str
    sources_searched: tuple[str, ...] = ()
    filters_interpreted: dict[str, object] = Field(default_factory=dict)
    candidates: tuple[DiscoveredCandidate, ...] = ()
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @field_validator("agent_identity", "instruction")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "DiscoveryRun field")
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError(f"DiscoveryRun.{field_name} must not be empty")
        return stripped

    @property
    def imported_count(self) -> int:
        return sum(
            1 for candidate in self.candidates if candidate.outcome is DiscoveryOutcome.IMPORTED
        )

    @property
    def skipped_count(self) -> int:
        return sum(
            1 for candidate in self.candidates if candidate.outcome is not DiscoveryOutcome.IMPORTED
        )
