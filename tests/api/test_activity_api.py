from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.domain.activity import ActivityEvent, ActorKind, MutationAction
from personal_graph_os.domain.identifiers import WorkspaceId


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(tmp_path / "test-workspace.db")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def _seed_events(app: FastAPI, workspace_id: str, count: int) -> list[ActivityEvent]:
    repository = app.state.activity_event_repository
    events = [
        ActivityEvent(
            workspace_id=WorkspaceId(workspace_id),
            actor_kind=ActorKind.HUMAN,
            actor_name="human/local-user/rest",
            source="rest",
            entity_type="node",
            entity_id=f"node-{index}",
            action=MutationAction.CREATED,
            occurred_at=datetime(2026, 1, 1, 0, index, tzinfo=UTC),
        )
        for index in range(count)
    ]
    for event in events:
        repository.save(event)
    return events


def test_list_activity_events_orders_newest_first_and_bounds_page(
    app: FastAPI, client: TestClient
) -> None:
    workspace_id = _workspace_id(client)
    _seed_events(app, workspace_id, 3)

    response = client.get(f"/activity-events?workspace_id={workspace_id}&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 2
    assert body["events"][0]["entity_id"] == "node-2"
    assert body["events"][1]["entity_id"] == "node-1"
    assert body["next_cursor"] is not None


def test_list_activity_events_cursor_reaches_remaining_page(
    app: FastAPI, client: TestClient
) -> None:
    workspace_id = _workspace_id(client)
    _seed_events(app, workspace_id, 3)

    first_page = client.get(f"/activity-events?workspace_id={workspace_id}&limit=2").json()
    second_page = client.get(
        f"/activity-events?workspace_id={workspace_id}&limit=2&cursor={first_page['next_cursor']}"
    ).json()

    assert [event["entity_id"] for event in second_page["events"]] == ["node-0"]
    assert second_page["next_cursor"] is None


def test_get_activity_event_returns_full_detail(app: FastAPI, client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    [event] = _seed_events(app, workspace_id, 1)

    response = client.get(f"/activity-events/{event.id}?workspace_id={workspace_id}")

    assert response.status_code == 200
    assert response.json()["id"] == event.id


def test_get_activity_event_404s_for_unknown_id(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.get(f"/activity-events/does-not-exist?workspace_id={workspace_id}")

    assert response.status_code == 404


def test_get_activity_event_404s_for_cross_workspace_id(app: FastAPI, client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    [event] = _seed_events(app, workspace_id, 1)

    response = client.get(f"/activity-events/{event.id}?workspace_id=some-other-workspace")

    assert response.status_code == 404
