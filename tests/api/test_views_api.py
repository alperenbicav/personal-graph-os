from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "test-workspace.db")
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace(client: TestClient) -> dict:
    return client.get("/workspace").json()


def test_evaluate_table_returns_captured_nodes(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Task A"},
    )

    response = client.post("/views/table", json={"workspace_id": workspace["id"], "query": {}})

    assert response.status_code == 200
    assert [row["node"]["title"] for row in response.json()] == ["Task A"]


def test_evaluate_kanban_groups_by_node_status(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    status_id = workspace["node_types"][0]["status_definitions"][0]["id"]
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Task A"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"status_id": status_id})

    response = client.post(
        "/views/kanban", json={"workspace_id": workspace["id"], "group_by": "status_id"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["node"]["title"] for row in body[status_id]] == ["Task A"]


def test_evaluate_table_via_saved_view_id_replays_the_persisted_query(
    client: TestClient,
) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Task A"},
    )
    saved_view = client.post(
        "/saved-views",
        json={"workspace_id": workspace["id"], "name": "Inbox", "view_kind": "table", "query": {}},
    ).json()

    response = client.post(
        "/views/table",
        json={"workspace_id": workspace["id"], "saved_view_id": saved_view["id"]},
    )

    assert response.status_code == 200
    assert [row["node"]["title"] for row in response.json()] == ["Task A"]


def test_an_edit_is_immediately_consistent_across_every_projection(client: TestClient) -> None:
    """04.3 acceptance: table/Kanban/timeline/search/canvas/reloaded saved view are all
    projections over the same canonical repository state, so an edit must be visible in every
    one of them without any extra sync step."""
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    status_id = workspace["node_types"][0]["status_definitions"][0]["id"]
    default_canvas_id = client.get("/workspace/default-canvas-id").json()

    node = client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Draft"},
    ).json()
    client.post(
        f"/canvases/{default_canvas_id}/placements",
        json={"node_id": node["id"], "position_x": 0, "position_y": 0},
    )
    saved_view = client.post(
        "/saved-views",
        json={
            "workspace_id": workspace["id"],
            "name": "All tasks",
            "view_kind": "table",
            "query": {},
        },
    ).json()

    client.patch(f"/nodes/{node['id']}", json={"title": "Published", "status_id": status_id})

    canvas_placements = client.get(f"/canvases/{default_canvas_id}/placements").json()
    canvas_node_ids = {placement["node_id"] for placement in canvas_placements}
    assert node["id"] in canvas_node_ids
    assert (
        client.get("/nodes", params={"workspace_id": workspace["id"]}).json()[0]["title"]
        == "Published"
    )

    search_results = client.get(
        "/search", params={"workspace_id": workspace["id"], "q": "published"}
    ).json()
    assert [r["node"]["title"] for r in search_results] == ["Published"]

    table_rows = client.post(
        "/views/table", json={"workspace_id": workspace["id"], "query": {}}
    ).json()
    assert [row["node"]["title"] for row in table_rows] == ["Published"]

    kanban = client.post(
        "/views/kanban", json={"workspace_id": workspace["id"], "group_by": "status_id"}
    ).json()
    assert [row["node"]["title"] for row in kanban[status_id]] == ["Published"]

    timeline = client.post(
        "/views/timeline",
        json={"workspace_id": workspace["id"], "date_field": "created_at"},
    ).json()
    assert [row["node"]["title"] for row in timeline] == ["Published"]

    reloaded_saved_view = client.post(
        "/views/table",
        json={"workspace_id": workspace["id"], "saved_view_id": saved_view["id"]},
    ).json()
    assert [row["node"]["title"] for row in reloaded_saved_view] == ["Published"]
