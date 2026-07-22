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


def _task_type_id(client: TestClient) -> str:
    workspace = client.get("/workspace").json()
    return next(nt for nt in workspace["node_types"] if nt["name"] == "Task")["id"]


def test_creating_a_node_over_rest_atomically_records_exactly_one_activity_event(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)

    created = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"}
    ).json()

    page = client.get(f"/activity-events?workspace_id={workspace_id}").json()
    matching = [event for event in page["events"] if event["entity_id"] == created["id"]]
    assert len(matching) == 1
    event = matching[0]
    assert event["entity_type"] == "node"
    assert event["action"] == "created"
    assert event["source"] == "rest"
    assert event["actor_name"] == "human/local-user/rest"
    assert event["is_undoable"] is True

    detail = client.get(f"/activity-events/{event['id']}?workspace_id={workspace_id}").json()
    assert detail["before_state"] is None
    assert detail["after_state"]["title"] == "x"


def test_updating_a_node_over_rest_records_before_and_after_state(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    created = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "before"},
    ).json()

    client.patch(f"/nodes/{created['id']}", json={"title": "after"})

    page = client.get(f"/activity-events?workspace_id={workspace_id}").json()
    updated_events = [
        event
        for event in page["events"]
        if event["entity_id"] == created["id"] and event["action"] == "updated"
    ]
    assert len(updated_events) == 1
    detail = client.get(
        f"/activity-events/{updated_events[0]['id']}?workspace_id={workspace_id}"
    ).json()
    assert detail["before_state"]["title"] == "before"
    assert detail["after_state"]["title"] == "after"


def test_a_no_op_resource_update_over_rest_never_invents_an_activity_event(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)
    resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper-noop",
        },
    ).json()
    # Same lifecycle_status as the resource already has -- a genuine no-op.
    client.patch(
        f"/resources/{resource['id']}",
        json={"lifecycle_status": resource["lifecycle_status"]},
    )

    page_after = client.get(f"/activity-events?workspace_id={workspace_id}").json()
    updated_events = [
        event
        for event in page_after["events"]
        if event["entity_id"] == resource["id"] and event["action"] == "updated"
    ]
    assert updated_events == []
