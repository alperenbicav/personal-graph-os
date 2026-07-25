from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app

_ENV = {
    "PGOS_WORK_PLANNING_PROVIDER": "http_chat",
    "PGOS_WORK_PLANNING_BASE_URL": "https://example.test",
    "PGOS_WORK_PLANNING_API_KEY": "test-key",
    "PGOS_WORK_PLANNING_MODEL": "test-model",
}


def _mock_chat_transport(*, is_justified: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if not is_justified:
            content = {"is_justified": False, "reason": "too small"}
        else:
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
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    return httpx.MockTransport(handler)


def _counting_mock_chat_transport(*, is_justified: bool) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []
    inner = _mock_chat_transport(is_justified=is_justified)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return inner.handle_request(request)

    return httpx.MockTransport(handler), calls


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        tmp_path / "test-workspace.db", work_planning_provider_transport=_mock_chat_transport()
    )
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def test_plan_work_capture_reaches_the_configured_provider_and_persists_the_hierarchy(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "manual",
            "request_id": "req-1",
            "actor_name": "agent:test",
            "payload_kind": "text",
            "text": "plan this",
            "intent": "plan_work",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["document_id"] is not None
    assert body["plan"] is not None
    assert body["plan"]["plan_version"]["body_markdown"] == "body from the configured adapter"
    assert len(body["plan"]["stories"]) == 1
    assert len(body["plan"]["tasks"]) == 1


def test_save_raw_capture_never_plans(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "manual",
            "request_id": "req-1",
            "actor_name": "agent:test",
            "payload_kind": "text",
            "text": "just a note, nothing more",
            "intent": "save_raw",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["document_id"] is not None
    assert body["plan"] is None


def test_plan_work_capture_with_an_unjustified_result_leaves_only_the_raw_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        tmp_path / "unjustified-workspace.db",
        work_planning_provider_transport=_mock_chat_transport(is_justified=False),
    )
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace_id = client.get("/workspace").json()["id"]

    response = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "manual",
            "request_id": "req-1",
            "actor_name": "agent:test",
            "payload_kind": "text",
            "text": "plan this",
            "intent": "plan_work",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["document_id"] is not None
    assert body["plan"] is None


def test_plan_work_capture_leaves_operation_pending_when_unconfigured(tmp_path: Path) -> None:
    app = create_app(tmp_path / "unconfigured-workspace.db")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace_id = client.get("/workspace").json()["id"]

    response = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "manual",
            "request_id": "req-1",
            "actor_name": "agent:test",
            "payload_kind": "text",
            "text": "plan this",
            "intent": "plan_work",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capture"]["pending_operations"] == ["plan"]
    assert body["plan"] is None


def test_replaying_the_same_capture_request_reuses_the_original_plan(client: TestClient) -> None:
    """Review finding S5-R04: an identical request must not create a second Epic/Story/Task tree
    or invoke the provider again."""
    workspace_id = _workspace_id(client)
    payload = {
        "workspace_id": workspace_id,
        "source": "manual",
        "request_id": "req-1",
        "actor_name": "agent:test",
        "payload_kind": "text",
        "text": "plan this",
        "intent": "plan_work",
    }

    first = client.post("/capture", json=payload)
    second = client.post("/capture", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["capture"]["ingestion_job_id"] == second_body["capture"]["ingestion_job_id"]
    assert second_body["capture"]["was_replayed"] is True
    assert first_body["plan"]["epic"]["id"] == second_body["plan"]["epic"]["id"]


def test_replaying_an_unjustified_plan_request_does_not_reinvoke_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding S5-R04's remaining gap: a "do not plan" decision must be receipted too, so
    an identical replay never calls the configured provider a second time."""
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    transport, calls = _counting_mock_chat_transport(is_justified=False)
    app = create_app(
        tmp_path / "unjustified-replay-workspace.db", work_planning_provider_transport=transport
    )
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace_id = _workspace_id(client)
    payload = {
        "workspace_id": workspace_id,
        "source": "manual",
        "request_id": "req-1",
        "actor_name": "agent:test",
        "payload_kind": "text",
        "text": "plan this",
        "intent": "plan_work",
    }

    first = client.post("/capture", json=payload)
    second = client.post("/capture", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["capture"]["ingestion_job_id"] == second_body["capture"]["ingestion_job_id"]
    assert second_body["capture"]["was_replayed"] is True
    assert first_body["plan"] is None
    assert second_body["plan"] is None
    assert len(calls) == 1
