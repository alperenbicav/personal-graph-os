from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from personal_graph_os.api.app import create_app
from personal_graph_os.infrastructure.mcp.gateway import AgentGatewayService
from personal_graph_os.infrastructure.mcp.server import _HANDLERS, _TOOLS


@pytest.mark.anyio
async def test_mcp_agent_tools_and_server_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGOS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PGOS_LLM_API_KEY", "test-openai-key")

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Analyzed graph context successfully."}}]},
        )

    app = create_app(
        tmp_path / "test.db",
        llm_provider_transport=httpx.MockTransport(mock_transport),
    )

    agent_repo = app.state.agent_repository
    agents = agent_repo.list_agents()
    assert len(agents) >= 3

    research_agent = next(a for a in agents if a.name == "Research-Agent")
    agent_id = research_agent.id

    # Test MCP server tool listing
    tool_names = {t.name for t in _TOOLS}
    assert "pgos_list_agents" in tool_names
    assert "agents.list" in tool_names
    assert "pgos_message_agent" in tool_names
    assert "agents.message" in tool_names
    assert "pgos_run_agent" in tool_names
    assert "agents.run" in tool_names

    # Test handler dispatch directly
    # 1. agents.list handler
    list_handler = _HANDLERS["agents.list"]
    gateway = AgentGatewayService(
        app.state.workspace_repository,
        app.state.node_repository,
        app.state.edge_repository,
        app.state.resource_repository,
        app.state.search_service,
        app.state.file_service,
        node_service=app.state.node_service,
        edge_service=app.state.edge_service,
        resource_service=app.state.resource_service,
        workflow_chain_service=app.state.workflow_chain_service,
        discovery_service=app.state.discovery_service,
        context_pack_service=app.state.context_pack_service,
        activity_service=app.state.activity_service,
        document_service=app.state.document_service,
        work_item_service=app.state.work_item_service,
        enrichment_service=app.state.enrichment_service,
        extraction_service=app.state.extraction_service,
        capture_planning_orchestrator=app.state.capture_planning_orchestrator,
        clickup_service=app.state.clickup_service,
        agent_service=app.state.agent_service,
        unit_of_work_factory=lambda: app.state.node_service._unit_of_work_factory(),
    )

    list_res = list_handler(gateway, {})
    assert "agents" in list_res
    agents_list = list_res["agents"]
    assert isinstance(agents_list, list)
    assert len(agents_list) >= 3

    # 2. agents.message handler
    msg_handler = _HANDLERS["agents.message"]
    msg_res = msg_handler(gateway, {"agent_id": str(agent_id), "content": "What is attention?"})
    assert msg_res["reply"] == "Analyzed graph context successfully."
    assert msg_res["run_id"]

    # Verify run logged
    runs = agent_repo.list_runs_for_agent(agent_id)
    assert len(runs) >= 1
    assert runs[0].action == "message"

    # 3. agents.run handler
    run_handler = _HANDLERS["agents.run"]
    default_ws_id = str(app.state.default_workspace_id)
    run_res = run_handler(
        gateway,
        {
            "agent_id": str(agent_id),
            "task": "Synthesize knowledge nodes",
            "workspace_id": default_ws_id,
        },
    )
    assert run_res["reply"] == "Analyzed graph context successfully."
    assert run_res["run_id"]

    # Verify run logged
    runs_after = agent_repo.list_runs_for_agent(agent_id)
    assert len(runs_after) >= 2
    assert runs_after[0].action == "run"
