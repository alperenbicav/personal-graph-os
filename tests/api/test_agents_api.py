from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "test-workspace.db")
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def test_unauthenticated_request_returns_401(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    unauthed = TestClient(app)
    response = unauthed.get("/agents")
    assert response.status_code == 401


def test_list_default_seeded_agents(client: TestClient) -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) >= 3
    names = {a["name"] for a in agents}
    assert "Research-Agent" in names
    assert "Ingest-Agent" in names
    assert "Plan-Agent" in names


def test_crud_agents(client: TestClient) -> None:
    # 1. Create
    create_res = client.post(
        "/agents",
        json={
            "name": "Coder-Agent",
            "emoji": "💻",
            "system_prompt": "You write clean code.",
            "tool_allowlist": ["fs_read", "fs_write"],
            "model": "gpt-4o",
            "write_mode": "direct",
        },
    )
    assert create_res.status_code == 201
    created = create_res.json()
    agent_id = created["id"]
    assert created["name"] == "Coder-Agent"
    assert created["emoji"] == "💻"
    assert created["tool_allowlist"] == ["fs_read", "fs_write"]
    assert created["write_mode"] == "direct"
    assert created["model"] == "gpt-4o"

    # 2. Get
    get_res = client.get(f"/agents/{agent_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Coder-Agent"

    # 3. Patch
    patch_res = client.patch(
        f"/agents/{agent_id}",
        json={
            "name": "Senior-Coder-Agent",
            "emoji": "🚀",
            "system_prompt": "You write robust code.",
            "write_mode": "proposal",
        },
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["name"] == "Senior-Coder-Agent"
    assert patched["emoji"] == "🚀"
    assert patched["system_prompt"] == "You write robust code."
    assert patched["write_mode"] == "proposal"
    assert patched["model"] == "gpt-4o"

    # 4. Delete
    del_res = client.delete(f"/agents/{agent_id}")
    assert del_res.status_code == 204

    # 5. Get after delete
    get_after = client.get(f"/agents/{agent_id}")
    assert get_after.status_code == 404


def test_get_unknown_agent_returns_404(client: TestClient) -> None:
    response = client.get("/agents/non-existent-agent-id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_send_message_unconfigured_llm_returns_503(client: TestClient) -> None:
    agents = client.get("/agents").json()
    agent_id = agents[0]["id"]

    response = client.post(
        f"/agents/{agent_id}/message",
        json={"content": "Hello agent"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "LLM provider not configured"


def test_send_message_streaming_sse_openai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGOS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PGOS_LLM_API_KEY", "mock-openai-key")

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        sse_content = (
            b'data: {"choices": [{"delta": {"content": "Analysis"}}]}\n\n'
            b'data: {"choices": [{"delta": {"content": " complete."}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        return httpx.Response(200, content=sse_content)

    transport = httpx.MockTransport(mock_transport)
    app = create_app(tmp_path / "test-workspace.db", llm_provider_transport=transport)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})

    agents = client.get("/agents").json()
    agent_id = agents[0]["id"]

    response = client.post(
        f"/agents/{agent_id}/message",
        json={"content": "Analyze this graph."},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text_content = response.text
    assert "Analysis" in text_content
    assert "complete." in text_content
    assert "[DONE]" in text_content

    # Verify run logged
    runs_res = client.get(f"/agents/{agent_id}/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json()
    assert len(runs) >= 1
    assert runs[0]["action"] == "message"
    assert "Analysis complete." in runs[0]["summary"]


def test_send_message_json_fallback_anthropic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGOS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("PGOS_LLM_API_KEY", "mock-anthropic-key")

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "Anthropic synthesis result."}]},
        )

    transport = httpx.MockTransport(mock_transport)
    app = create_app(tmp_path / "test-workspace.db", llm_provider_transport=transport)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})

    agents = client.get("/agents").json()
    agent_id = agents[0]["id"]

    # Header Accept: application/json
    response = client.post(
        f"/agents/{agent_id}/message",
        json={"content": "Summarize research."},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Anthropic synthesis result."
    assert body["run_id"]

    # Verify run logged
    runs_res = client.get(f"/agents/{agent_id}/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json()
    assert len(runs) >= 1
    assert runs[0]["summary"] == "Anthropic synthesis result."
