from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.application.agent_service import AgentService
from personal_graph_os.application.llm_provider import LlmResponse, LlmToolCall
from personal_graph_os.application.tool_bridge import ToolBridge
from personal_graph_os.domain.agents import AgentRunStatus, AgentWriteMode
from personal_graph_os.domain.identifiers import AgentRunId, WorkItemId, WorkspaceId
from personal_graph_os.domain.work_items import WorkItemKind, WorkItemStatus, WorkItemType


class StubToolCallingProvider:
    def __init__(self, turns: list[LlmResponse]) -> None:
        self.turns = list(turns)
        self.calls_recorded: list[dict[str, Any]] = []

    async def chat_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> LlmResponse:
        self.calls_recorded.append({"messages": messages, "tools": tools, "model": model})
        if not self.turns:
            return LlmResponse(content="No more scripted turns.")
        return self.turns.pop(0)

    async def complete(
        self, *, system_prompt: str, user_message: str, model: str | None = None
    ) -> str:
        turn = await self.chat_turn(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            model=model,
        )
        return turn.content or ""

    async def stream(self, *, system_prompt: str, user_message: str, model: str | None = None):
        text = await self.complete(
            system_prompt=system_prompt, user_message=user_message, model=model
        )
        yield text


@pytest.fixture
def app_and_client(tmp_path: Path):
    app = create_app(tmp_path / "test-workspace.db")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return app, client


def test_tool_bridge_allowlist_enforcement(app_and_client):
    app, _ = app_and_client
    tool_bridge: ToolBridge = app.state.tool_bridge
    workspace_id: WorkspaceId = app.state.default_workspace_id

    # Agent with only tasks.list_tasks allowed
    restricted_agent = app.state.agent_service.create_agent(
        name="Restricted",
        system_prompt="Read only",
        tool_allowlist=["tasks.list_tasks"],
        write_mode=AgentWriteMode.PROPOSAL,
    )

    # Allowed tool executes
    res1 = tool_bridge.execute(
        "tasks.list_tasks",
        {},
        agent=restricted_agent,
        workspace_id=workspace_id,
    )
    assert res1.status == "ok"
    assert isinstance(res1.output, list)

    # Disallowed tool returns error
    res2 = tool_bridge.execute(
        "tasks.delete",
        {"task_id": "item-1"},
        agent=restricted_agent,
        workspace_id=workspace_id,
    )
    assert res2.status == "error"
    assert "not in the allowlist" in res2.output["error"]


def test_tool_bridge_crud_and_proposal_mechanics(app_and_client):
    app, _ = app_and_client
    tool_bridge: ToolBridge = app.state.tool_bridge
    workspace_id: WorkspaceId = app.state.default_workspace_id
    agent_service: AgentService = app.state.agent_service

    proposal_agent = agent_service.create_agent(
        name="Planner",
        system_prompt="Plan and propose",
        tool_allowlist=["*"],
        write_mode=AgentWriteMode.PROPOSAL,
    )

    # 1. tasks.create applies immediately in proposal mode
    create_res = tool_bridge.execute(
        "tasks.create",
        {"title": "Deploy Agent Core", "kind": "story", "work_type": "feature"},
        agent=proposal_agent,
        workspace_id=workspace_id,
    )
    print("DEBUG create_res:", create_res)
    assert create_res.status == "ok", create_res.output
    task_id = create_res.output["id"]
    assert create_res.output["title"] == "Deploy Agent Core"

    # 2. tasks.get retrieves task details
    get_res = tool_bridge.execute(
        "tasks.get",
        {"task_id": task_id},
        agent=proposal_agent,
        workspace_id=workspace_id,
    )
    assert get_res.status == "ok"
    assert get_res.output["title"] == "Deploy Agent Core"

    # 3. tasks.set_status creates a pending_review proposal
    status_res = tool_bridge.execute(
        "tasks.set_status",
        {"task_id": task_id, "status": "in_progress"},
        agent=proposal_agent,
        workspace_id=workspace_id,
    )
    assert status_res.status == "pending_review"
    assert status_res.is_proposal is True
    run_id = status_res.run_id
    assert run_id is not None

    # Verify task status was NOT modified yet
    item_before = app.state.work_item_service.get(WorkItemId(task_id))
    assert item_before.status == WorkItemStatus.BACKLOG

    # Approve proposal -> verifies status changes to in_progress
    approved_run = agent_service.approve_run(AgentRunId(run_id))
    assert approved_run.status == AgentRunStatus.APPLIED
    item_after = app.state.work_item_service.get(WorkItemId(task_id))
    assert item_after.status == WorkItemStatus.IN_PROGRESS

    # 4. tasks.delete creates pending_review proposal, reject discards it
    del_res = tool_bridge.execute(
        "tasks.delete",
        {"task_id": task_id},
        agent=proposal_agent,
        workspace_id=workspace_id,
    )
    assert del_res.status == "pending_review"
    assert del_res.run_id is not None
    del_run_id = del_res.run_id

    # Reject proposal
    rejected_run = agent_service.reject_run(AgentRunId(del_run_id))
    assert rejected_run.status == AgentRunStatus.REJECTED

    # Verify item still exists and is not archived
    item_still_alive = app.state.work_item_service.get(WorkItemId(task_id))
    assert item_still_alive.is_archived is False


@pytest.mark.anyio
async def test_agent_loop_with_tool_calls_and_proposals(app_and_client):
    app, _ = app_and_client
    workspace_id: WorkspaceId = app.state.default_workspace_id

    # Create 2 initial tasks
    t1, _ = app.state.work_item_service.create(
        workspace_id,
        title="Task 1",
        kind=WorkItemKind.TASK,
        work_type=WorkItemType.FEATURE,
        source="test",
    )
    t2, _ = app.state.work_item_service.create(
        workspace_id,
        title="Task 2",
        kind=WorkItemKind.TASK,
        work_type=WorkItemType.FEATURE,
        source="test",
    )

    # Scripted LLM turns:
    # Turn 1: Model requests tasks.list_tasks
    # Turn 2: Model requests tasks.delete for t1
    # Turn 3: Model yields final summary text
    scripted_turns = [
        LlmResponse(
            content=None,
            tool_calls=[LlmToolCall(id="call_1", name="tasks.list_tasks", arguments={})],
        ),
        LlmResponse(
            content=None,
            tool_calls=[
                LlmToolCall(
                    id="call_2",
                    name="tasks.delete",
                    arguments={"task_id": str(t1.id)},
                )
            ],
        ),
        LlmResponse(
            content="Task 1 silinmek üzere önerildi. Onay bekleniyor.",
            tool_calls=[],
        ),
    ]

    provider = StubToolCallingProvider(scripted_turns)
    agent = app.state.agent_service.create_agent(
        name="TaskMaster",
        system_prompt="Manage tasks",
        tool_allowlist=["tasks.list_tasks", "tasks.delete"],
        write_mode=AgentWriteMode.PROPOSAL,
    )

    agent_service = AgentService(
        repository=app.state.agent_repository,
        llm_provider=provider,
        tool_bridge=app.state.tool_bridge,
        workspace_id=workspace_id,
    )

    reply, runs = await agent_service.run_agent_loop(agent, "Tüm taskleri listele ve ilkini sil")

    assert "silinmek üzere önerildi" in reply
    assert len(runs) >= 2  # pending_review delete run + message run

    # Verify a pending_review run was created
    pending_runs = [r for r in runs if r.status == AgentRunStatus.PENDING_REVIEW]
    assert len(pending_runs) == 1
    assert pending_runs[0].action == "tasks.delete"


@pytest.mark.anyio
async def test_agent_loop_max_iterations_bound(app_and_client):
    app, _ = app_and_client
    workspace_id: WorkspaceId = app.state.default_workspace_id

    # Provider that loops forever with tool calls
    infinite_tool_calls = [
        LlmResponse(
            content=None,
            tool_calls=[LlmToolCall(id=f"call_{i}", name="tasks.list_tasks", arguments={})],
        )
        for i in range(10)
    ]

    provider = StubToolCallingProvider(infinite_tool_calls)
    agent = app.state.agent_service.create_agent(
        name="LoopAgent",
        system_prompt="Loop test",
        tool_allowlist=["tasks.list_tasks"],
        write_mode=AgentWriteMode.PROPOSAL,
    )

    agent_service = AgentService(
        repository=app.state.agent_repository,
        llm_provider=provider,
        tool_bridge=app.state.tool_bridge,
        workspace_id=workspace_id,
    )

    reply, _ = await agent_service.run_agent_loop(agent, "Keep listing tasks", max_iterations=4)
    assert "iteration limit" in reply.lower()


def test_dock_approve_reject_api_endpoints(app_and_client):
    app, client = app_and_client
    tool_bridge: ToolBridge = app.state.tool_bridge
    workspace_id: WorkspaceId = app.state.default_workspace_id

    task, _ = app.state.work_item_service.create(
        workspace_id,
        title="Test Task for API",
        kind=WorkItemKind.TASK,
        work_type=WorkItemType.FEATURE,
        source="test",
    )

    agent = app.state.agent_service.create_agent(
        name="ApiAgent",
        system_prompt="Test API",
        tool_allowlist=["*"],
        write_mode="proposal",
    )

    # 1. Create a proposal via ToolBridge
    res = tool_bridge.execute(
        "tasks.delete",
        {"task_id": str(task.id)},
        agent=agent,
        workspace_id=workspace_id,
    )
    run_id = res.run_id
    assert run_id is not None

    # 2. List recent runs
    runs_res = client.get("/agents/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json()
    assert any(r["id"] == run_id and r["status"] == "pending_review" for r in runs)

    # 3. Approve the run via API
    approve_res = client.post(f"/agents/runs/{run_id}/approve")
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "applied"

    # Verify task was actually deleted/archived
    archived_task = app.state.work_item_service.get(task.id)
    assert archived_task.is_archived is True
