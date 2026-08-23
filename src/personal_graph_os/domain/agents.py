"""Domain models for the Agent Core (EP-2026-014 ST-01).

Agents represent configured AI personas with distinct system prompts, allowed tools,
models, and write modes. Agent runs track execution history, messages, tool runs,
and proposed or applied state mutations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import AgentId, AgentRunId, new_id


class AgentWriteMode(StrEnum):
    PROPOSAL = "proposal"
    DIRECT = "direct"


class AgentRunStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPLIED = "applied"
    REJECTED = "rejected"


class Agent(BaseModel):
    id: AgentId
    name: str
    emoji: str = "🤖"
    system_prompt: str
    tool_allowlist: list[str] = Field(default_factory=list)
    model: str | None = None
    write_mode: AgentWriteMode = AgentWriteMode.PROPOSAL
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        name: str,
        system_prompt: str,
        *,
        emoji: str = "🤖",
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
        write_mode: AgentWriteMode | str = AgentWriteMode.PROPOSAL,
        agent_id: AgentId | None = None,
        created_at: datetime | None = None,
    ) -> Agent:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise InvariantViolationError("Agent name must not be empty")
        cleaned_prompt = system_prompt.strip()
        if not cleaned_prompt:
            raise InvariantViolationError("Agent system_prompt must not be empty")

        if isinstance(write_mode, str):
            write_mode = AgentWriteMode(write_mode)

        return cls(
            id=agent_id or AgentId(new_id()),
            name=cleaned_name,
            emoji=emoji.strip() or "🤖",
            system_prompt=cleaned_prompt,
            tool_allowlist=tool_allowlist or [],
            model=model.strip() if model and model.strip() else None,
            write_mode=write_mode,
            created_at=created_at or datetime.now(UTC),
        )


class AgentRun(BaseModel):
    id: AgentRunId
    agent_id: AgentId
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    status: AgentRunStatus = AgentRunStatus.APPLIED
    summary: str
    diff_json: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        agent_id: AgentId,
        action: str,
        summary: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        status: AgentRunStatus | str = AgentRunStatus.APPLIED,
        diff_json: str | None = None,
        run_id: AgentRunId | None = None,
        created_at: datetime | None = None,
    ) -> AgentRun:
        cleaned_action = action.strip()
        if not cleaned_action:
            raise InvariantViolationError("AgentRun action must not be empty")
        if isinstance(status, str):
            status = AgentRunStatus(status)

        return cls(
            id=run_id or AgentRunId(new_id()),
            agent_id=agent_id,
            action=cleaned_action,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            summary=summary,
            diff_json=diff_json,
            created_at=created_at or datetime.now(UTC),
        )


class AgentNotFoundError(Exception):
    """Raised when an agent cannot be found by identifier."""


class AgentRunNotFoundError(Exception):
    """Raised when an agent run cannot be found by identifier."""
