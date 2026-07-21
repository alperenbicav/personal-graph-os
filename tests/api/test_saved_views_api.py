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


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def test_create_saved_view_returns_201_and_persists_query(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/saved-views",
        json={
            "workspace_id": workspace_id,
            "name": "Inbox",
            "view_kind": "table",
            "query": {
                "filters": [{"field": "is_archived", "operator": "eq", "value": False}],
                "sort": [{"field": "created_at", "direction": "desc"}],
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Inbox"
    assert body["view_kind"] == "table"
    assert body["filter_definition"]["filters"][0]["field"] == "is_archived"


def test_list_saved_views_returns_only_that_workspace(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    client.post(
        "/saved-views",
        json={"workspace_id": workspace_id, "name": "Inbox", "view_kind": "table", "query": {}},
    )

    response = client.get("/saved-views", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    assert [view["name"] for view in response.json()] == ["Inbox"]


def test_update_saved_view_renames_it(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/saved-views",
        json={"workspace_id": workspace_id, "name": "Inbox", "view_kind": "table", "query": {}},
    ).json()

    response = client.patch(f"/saved-views/{created['id']}", json={"name": "Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_delete_saved_view_removes_it(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/saved-views",
        json={"workspace_id": workspace_id, "name": "Inbox", "view_kind": "table", "query": {}},
    ).json()

    delete_response = client.delete(f"/saved-views/{created['id']}")
    assert delete_response.status_code == 204

    get_response = client.get(f"/saved-views/{created['id']}")
    assert get_response.status_code == 404


def test_get_unknown_saved_view_returns_404(client: TestClient) -> None:
    response = client.get("/saved-views/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
