"""Agent application service coordinating agent registry and LLM interactions
(EP-2026-014 ST-01/ST-02).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from personal_graph_os.application.llm_provider import (
    LlmNotConfiguredError,
    LlmProvider,
)
from personal_graph_os.application.repositories import AgentRepository
from personal_graph_os.application.tool_bridge import ToolBridge
from personal_graph_os.domain.agents import (
    Agent,
    AgentNotFoundError,
    AgentRun,
    AgentRunStatus,
    AgentWriteMode,
)
from personal_graph_os.domain.identifiers import AgentId, AgentRunId, WorkspaceId


class AgentService:
    def __init__(
        self,
        repository: AgentRepository,
        llm_provider: LlmProvider | None = None,
        uow_factory: Callable[[], Any] | None = None,
        *,
        tool_bridge: ToolBridge | None = None,
        workspace_id: WorkspaceId | None = None,
    ) -> None:
        self._repository = repository
        self._llm_provider = llm_provider
        self._uow_factory = uow_factory
        self._tool_bridge = tool_bridge
        self._workspace_id = workspace_id

    @property
    def llm_provider(self) -> LlmProvider | None:
        return self._llm_provider

    @property
    def tool_bridge(self) -> ToolBridge | None:
        return self._tool_bridge

    def set_tool_bridge(self, tool_bridge: ToolBridge, workspace_id: WorkspaceId) -> None:
        self._tool_bridge = tool_bridge
        self._workspace_id = workspace_id

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
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.agents.save_without_commit(agent)
        else:
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
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.agents.save_without_commit(updated_agent)
        else:
            self._repository.save_without_commit(updated_agent)
        return updated_agent

    def delete_agent(self, agent_id: AgentId) -> None:
        self.get_agent(agent_id)
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.agents.delete_without_commit(agent_id)
        else:
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
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.agents.record_run_without_commit(run)
        else:
            self._repository.record_run_without_commit(run)
        return run

    def get_run(self, run_id: AgentRunId) -> AgentRun:
        run = self._repository.get_run(run_id)
        if run is None:
            raise ValueError(f"Agent run {run_id} not found")
        return run

    def list_runs(self, agent_id: AgentId, limit: int = 50) -> list[AgentRun]:
        self.get_agent(agent_id)
        return list(self._repository.list_runs_for_agent(agent_id, limit=limit))

    def list_recent_runs(self, limit: int = 50) -> list[AgentRun]:
        return list(self._repository.list_recent_runs(limit=limit))

    def approve_run(
        self, run_id: AgentRunId, *, workspace_id: WorkspaceId | None = None
    ) -> AgentRun:
        run = self.get_run(run_id)
        if run.status != AgentRunStatus.PENDING_REVIEW:
            raise ValueError(
                f"Run {run_id} is not in pending_review status (current: {run.status})"
            )
        ws_id = workspace_id or self._workspace_id
        if ws_id is None:
            raise ValueError("Workspace ID required to apply proposal")
        if self._tool_bridge is not None:
            self._tool_bridge.apply_proposal(run, ws_id)
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                updated = uow.agents.update_run_status_without_commit(
                    run_id, AgentRunStatus.APPLIED
                )
        else:
            updated = self._repository.update_run_status_without_commit(
                run_id, AgentRunStatus.APPLIED
            )
        if updated is None:
            raise ValueError(f"Failed to update run status for {run_id}")
        return updated

    def reject_run(self, run_id: AgentRunId) -> AgentRun:
        run = self.get_run(run_id)
        if run.status != AgentRunStatus.PENDING_REVIEW:
            raise ValueError(
                f"Run {run_id} is not in pending_review status (current: {run.status})"
            )
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                updated = uow.agents.update_run_status_without_commit(
                    run_id, AgentRunStatus.REJECTED
                )
        else:
            updated = self._repository.update_run_status_without_commit(
                run_id, AgentRunStatus.REJECTED
            )
        if updated is None:
            raise ValueError(f"Failed to update run status for {run_id}")
        return updated

    async def complete_message(
        self,
        agent: Agent,
        content: str,
        *,
        system_prompt: str | None = None,
        workspace_id: WorkspaceId | None = None,
    ) -> str:
        final_text, _ = await self.run_agent_loop(
            agent,
            content,
            system_prompt=system_prompt,
            workspace_id=workspace_id,
        )
        return final_text

    async def run_agent_loop(
        self,
        agent: Agent,
        content: str,
        *,
        system_prompt: str | None = None,
        workspace_id: WorkspaceId | None = None,
        max_iterations: int = 6,
    ) -> tuple[str, list[AgentRun]]:
        if self._llm_provider is None:
            raise LlmNotConfiguredError("LLM provider not configured")
        ws_id = workspace_id or self._workspace_id

        prompt = system_prompt if system_prompt is not None else agent.system_prompt
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ]
        tools = (
            self._tool_bridge.get_openai_tools(agent.tool_allowlist)
            if self._tool_bridge and ws_id
            else None
        )
        runs: list[AgentRun] = []

        for _ in range(max_iterations):
            turn = await self._llm_provider.chat_turn(
                messages=messages,
                tools=tools,
                model=agent.model,
            )
            if turn.tool_calls and ws_id:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": turn.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in turn.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                for tc in turn.tool_calls:
                    if self._tool_bridge:
                        res = self._tool_bridge.execute(
                            tc.name,
                            tc.arguments,
                            agent=agent,
                            workspace_id=ws_id,
                        )
                        output_str = json.dumps(res.output, default=str)
                        if res.run_id:
                            try:
                                run_obj = self.get_run(AgentRunId(res.run_id))
                                runs.append(run_obj)
                            except Exception:
                                pass
                    else:
                        output_str = json.dumps({"error": "No tool bridge configured"})

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output_str,
                        }
                    )
                continue

            final_text = turn.content or "Task completed."
            run = self.record_run(
                agent_id=agent.id,
                action="message",
                summary=final_text[:200] if final_text else content[:200],
            )
            runs.append(run)
            return final_text, runs

        fallback_text = "I completed all possible steps within the iteration limit."
        run = self.record_run(
            agent_id=agent.id,
            action="message",
            summary=fallback_text[:200],
        )
        runs.append(run)
        return fallback_text, runs

    def complete_message_sync(
        self, agent: Agent, content: str, *, system_prompt: str | None = None
    ) -> str:
        if self._llm_provider is None:
            raise LlmNotConfiguredError("LLM provider not configured")
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        prompt = system_prompt if system_prompt is not None else agent.system_prompt
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(
                    asyncio.run,
                    self.complete_message(agent, content, system_prompt=prompt),
                ).result()
        else:
            return asyncio.run(
                self.complete_message(agent, content, system_prompt=prompt)
            )

    async def stream_message(
        self, agent: Agent, content: str, *, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        if self._llm_provider is None:
            raise LlmNotConfiguredError("LLM provider not configured")
        prompt = system_prompt if system_prompt is not None else agent.system_prompt
        async for chunk in self._llm_provider.stream(
            system_prompt=prompt,
            user_message=content,
            model=agent.model,
        ):
            yield chunk

