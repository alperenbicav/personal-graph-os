"""Agent application service coordinating agent registry and LLM interactions
(EP-2026-014 ST-01/ST-02).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from personal_graph_os.application.llm_provider import LlmNotConfiguredError, LlmProvider
from personal_graph_os.application.repositories import AgentRepository
from personal_graph_os.domain.agents import (
    Agent,
    AgentNotFoundError,
    AgentRun,
    AgentRunStatus,
    AgentWriteMode,
)
from personal_graph_os.domain.identifiers import AgentId


class AgentService:
    def __init__(
        self,
        repository: AgentRepository,
        llm_provider: LlmProvider | None = None,
        uow_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._repository = repository
        self._llm_provider = llm_provider
        self._uow_factory = uow_factory

    @property
    def llm_provider(self) -> LlmProvider | None:
        return self._llm_provider

    def list_agents(self) -> list[Agent]:
        return list(self._repository.list_agents())

    def get_agent(self, agent_id: AgentId) -> Agent:
        agent = self._repository.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"Agent {agent_id} not found")
        return agent

    def create_agent(
        self,
        name: str,
        system_prompt: str,
        *,
        emoji: str = "🤖",
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
        write_mode: AgentWriteMode | str = AgentWriteMode.PROPOSAL,
    ) -> Agent:
        agent = Agent.create(
            name,
            system_prompt,
            emoji=emoji,
            tool_allowlist=tool_allowlist,
            model=model,
            write_mode=write_mode,
        )
        self._repository.save_without_commit(agent)
        return agent

    def update_agent(
        self,
        agent_id: AgentId,
        *,
        name: str | None = None,
        emoji: str | None = None,
        system_prompt: str | None = None,
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
        write_mode: AgentWriteMode | str | None = None,
    ) -> Agent:
        agent = self.get_agent(agent_id)
        updates: dict[str, Any] = {}
        if name is not None:
            cleaned_name = name.strip()
            if not cleaned_name:
                raise ValueError("Agent name must not be empty")
            updates["name"] = cleaned_name
        if emoji is not None:
            updates["emoji"] = emoji.strip() or "🤖"
        if system_prompt is not None:
            cleaned_prompt = system_prompt.strip()
            if not cleaned_prompt:
                raise ValueError("Agent system_prompt must not be empty")
            updates["system_prompt"] = cleaned_prompt
        if tool_allowlist is not None:
            updates["tool_allowlist"] = list(tool_allowlist)
        if model is not None:
            updates["model"] = model.strip() if model.strip() else None
        if write_mode is not None:
            updates["write_mode"] = (
                AgentWriteMode(write_mode) if isinstance(write_mode, str) else write_mode
            )

        updated_agent = agent.model_copy(update=updates)
        self._repository.save_without_commit(updated_agent)
        return updated_agent

    def delete_agent(self, agent_id: AgentId) -> None:
        self.get_agent(agent_id)
        self._repository.delete_without_commit(agent_id)

    def record_run(
        self,
        agent_id: AgentId,
        action: str,
        summary: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        status: AgentRunStatus | str = AgentRunStatus.APPLIED,
        diff_json: str | None = None,
    ) -> AgentRun:
        run = AgentRun.create(
            agent_id=agent_id,
            action=action,
            summary=summary,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            diff_json=diff_json,
        )
        self._repository.record_run_without_commit(run)
        return run

    def list_runs(self, agent_id: AgentId, limit: int = 50) -> list[AgentRun]:
        self.get_agent(agent_id)
        return list(self._repository.list_runs_for_agent(agent_id, limit=limit))

    async def complete_message(self, agent: Agent, content: str) -> str:
        if self._llm_provider is None:
            raise LlmNotConfiguredError("LLM provider not configured")
        return await self._llm_provider.complete(
            system_prompt=agent.system_prompt,
            user_message=content,
            model=agent.model,
        )

    async def stream_message(self, agent: Agent, content: str) -> AsyncIterator[str]:
        if self._llm_provider is None:
            raise LlmNotConfiguredError("LLM provider not configured")
        async for chunk in self._llm_provider.stream(
            system_prompt=agent.system_prompt,
            user_message=content,
            model=agent.model,
        ):
            yield chunk
