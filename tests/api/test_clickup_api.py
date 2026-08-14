from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app


def _clickup_transport(
    *,
    task_id: str = "task-1",
    name: str = "Implement signup",
    description: str = "Add OAuth login",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v2/task/{task_id}":
            return httpx.Response(
                200,
                json={
                    "id": task_id,
                    "name": name,
                    "description": description,
                    "url": f"https://app.clickup.com/t/{task_id}",
                    "date_updated": 1720000000000,
                },
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _configured_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: httpx.MockTransport,
    work_planning: bool = False,
) -> TestClient:
    monkeypatch.setenv("PGOS_CLICKUP_API_TOKEN", "test-clickup-token")
    if work_planning:
        monkeypatch.setenv("PGOS_WORK_PLANNING_PROVIDER", "http_chat")
        monkeypatch.setenv("PGOS_WORK_PLANNING_BASE_URL", "https://example.test")
        monkeypatch.setenv("PGOS_WORK_PLANNING_API_KEY", "test-key")
        monkeypatch.setenv("PGOS_WORK_PLANNING_MODEL", "test-model")
    app = create_app(
        tmp_path / "clickup-workspace.db",
        clickup_transport=transport,
        work_planning_provider_transport=_mock_chat_transport(),
    )
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return client


def _mock_chat_transport() -> httpx.MockTransport:
    content = {
        "is_justified": True,
        "reason": "worth planning",
        "epic": {"title": "Epic", "work_type": "feature", "description": ""},
        "stories": [
            {
                "title": "Story",
                "work_type": "feature",
                "description": "",
                "tasks": [{"title": "Task", "work_type": "feature", "description": ""}],
            }
        ],
        "plan_title": "Plan",
        "plan_body_markdown": "body from the configured adapter",
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    return httpx.MockTransport(handler)


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def test_import_is_unavailable_without_a_configured_token(tmp_path: Path) -> None:
    app = create_app(tmp_path / "unconfigured-clickup.db")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace_id = _workspace_id(client)

    response = client.post(
        "/clickup/import",
        json={
            "workspace_id": workspace_id,
            "task_id": "task-1",
            "intent": "save_raw",
            "actor_name": "alperen",
        },
    )

    assert response.status_code == 503
    assert "PGOS_CLICKUP_API_TOKEN" in response.json()["detail"]


def test_save_raw_import_creates_a_raw_document_like_an_mcp_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch, transport=_clickup_transport())
    workspace_id = _workspace_id(client)

    response = client.post(
        "/clickup/import",
        json={
            "workspace_id": workspace_id,
            "task_id": "task-1",
            "intent": "save_raw",
            "actor_name": "alperen",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["document_id"] is not None
    assert body["capture"]["needs_clarification"] is False
    assert body["plan"] is None


def test_plan_work_import_plans_through_the_same_capture_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _configured_client(
        tmp_path, monkeypatch, transport=_clickup_transport(), work_planning=True
    )
    workspace_id = _workspace_id(client)

    response = client.post(
        "/clickup/import",
        json={
            "workspace_id": workspace_id,
            "task_id": "task-1",
            "intent": "plan_work",
            "actor_name": "alperen",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["pending_operations"] == ["plan"]
    assert body["plan"] is not None
    assert body["plan"]["plan_version"]["body_markdown"] == "body from the configured adapter"


def test_reimporting_the_same_task_id_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch, transport=_clickup_transport())
    workspace_id = _workspace_id(client)
    payload = {
        "workspace_id": workspace_id,
        "task_id": "task-1",
        "intent": "save_raw",
        "actor_name": "alperen",
    }

    first = client.post("/clickup/import", json=payload)
    second = client.post("/clickup/import", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert (
        first.json()["capture"]["ingestion_job_id"] == second.json()["capture"]["ingestion_job_id"]
    )
    assert second.json()["capture"]["was_replayed"] is True


def test_unknown_task_is_a_typed_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _configured_client(tmp_path, monkeypatch, transport=_clickup_transport())
    workspace_id = _workspace_id(client)

    response = client.post(
        "/clickup/import",
        json={
            "workspace_id": workspace_id,
            "task_id": "missing",
            "intent": "save_raw",
            "actor_name": "alperen",
        },
    )

    assert response.status_code == 404


def test_rejected_token_is_an_authorization_error_not_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for review finding S10-F03: an invalid token (ClickUp 401/403) is a distinct
    authorization failure (HTTP 401), not the unconfigured 503."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"err": "Unauthorized"})

    client = _configured_client(tmp_path, monkeypatch, transport=httpx.MockTransport(handler))
    workspace_id = _workspace_id(client)

    response = client.post(
        "/clickup/import",
        json={
            "workspace_id": workspace_id,
            "task_id": "task-1",
            "intent": "save_raw",
            "actor_name": "alperen",
        },
    )

    assert response.status_code == 401
