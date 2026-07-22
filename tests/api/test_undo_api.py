from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app


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


def _task_type_id(client: TestClient) -> str:
    workspace = client.get("/workspace").json()
    return next(nt for nt in workspace["node_types"] if nt["name"] == "Task")["id"]


def _latest_event_for(client: TestClient, workspace_id: str, entity_id: str, action: str) -> dict:
    page = client.get(f"/activity-events?workspace_id={workspace_id}").json()
    matches = [
        event
        for event in page["events"]
        if event["entity_id"] == entity_id and event["action"] == action
    ]
    assert matches, f"no {action} event found for {entity_id}"
    return matches[0]


def test_undo_a_node_update_restores_the_previous_title(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "before"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"title": "after"})
    update_event = _latest_event_for(client, workspace_id, node["id"], "updated")

    response = client.post(
        f"/activity-events/{update_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "accidental edit"},
    )

    assert response.status_code == 200
    compensating = response.json()
    assert compensating["reverses_event_id"] == update_event["id"]
    assert compensating["is_undoable"] is False
    assert compensating["reason"] == "accidental edit"

    restored = client.get("/nodes", params={"workspace_id": workspace_id}).json()
    restored_node = next(n for n in restored if n["id"] == node["id"])
    assert restored_node["title"] == "before"


def test_undo_a_node_create_soft_archives_it(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    ).json()
    create_event = _latest_event_for(client, workspace_id, node["id"], "created")

    response = client.post(
        f"/activity-events/{create_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "created by mistake"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "archived"
    listed = client.get(
        "/nodes", params={"workspace_id": workspace_id, "include_archived": True}
    ).json()
    assert next(n for n in listed if n["id"] == node["id"])["is_archived"] is True


def test_undo_an_edge_create_removes_it(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    workspace = client.get("/workspace").json()
    edge_type_id = workspace["edge_types"][0]["id"]
    node_a = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "a"}
    ).json()
    node_b = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "b"}
    ).json()
    edge = client.post(
        "/edges",
        json={
            "workspace_id": workspace_id,
            "edge_type_id": edge_type_id,
            "source_node_id": node_a["id"],
            "target_node_id": node_b["id"],
        },
    ).json()
    create_event = _latest_event_for(client, workspace_id, edge["id"], "created")

    response = client.post(
        f"/activity-events/{create_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "wrong connection"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "deleted"
    remaining = client.get("/edges", params={"workspace_id": workspace_id}).json()
    assert edge["id"] not in [e["id"] for e in remaining]


def test_undo_twice_returns_409_already_reversed(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "before"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"title": "after"})
    update_event = _latest_event_for(client, workspace_id, node["id"], "updated")

    first = client.post(
        f"/activity-events/{update_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "first undo"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/activity-events/{update_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "second undo"},
    )
    assert second.status_code == 409


def test_undo_a_stale_event_returns_409(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "v1"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"title": "v2"})
    first_update_event = _latest_event_for(client, workspace_id, node["id"], "updated")
    client.patch(f"/nodes/{node['id']}", json={"title": "v3"})

    response = client.post(
        f"/activity-events/{first_update_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "stale attempt"},
    )

    assert response.status_code == 409
    unchanged = client.get("/nodes", params={"workspace_id": workspace_id}).json()
    assert next(n for n in unchanged if n["id"] == node["id"])["title"] == "v3"


def test_undo_a_non_undoable_event_returns_409(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/node-types", json={"workspace_id": workspace_id, "name": "Widget"}
    ).json()
    create_event = _latest_event_for(client, workspace_id, created["id"], "created")

    response = client.post(
        f"/activity-events/{create_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": "trying anyway"},
    )

    assert response.status_code == 409


def test_undo_rejects_an_empty_reason(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    ).json()
    create_event = _latest_event_for(client, workspace_id, node["id"], "created")

    response = client.post(
        f"/activity-events/{create_event['id']}/undo?workspace_id={workspace_id}",
        json={"reason": ""},
    )

    assert response.status_code == 422
