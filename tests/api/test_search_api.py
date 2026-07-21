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


def test_search_finds_a_captured_node_by_title(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    client.post(
        "/nodes",
        json={
            "workspace_id": workspace["id"],
            "node_type_id": node_type_id,
            "title": "Attention Is All You Need",
        },
    )

    response = client.get("/search", params={"workspace_id": workspace["id"], "q": "attention"})

    assert response.status_code == 200
    body = response.json()
    assert [r["node"]["title"] for r in body] == ["Attention Is All You Need"]


def test_search_excludes_archived_nodes_unless_requested(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Findable"},
    ).json()
    client.delete(f"/nodes/{node['id']}")

    default = client.get("/search", params={"workspace_id": workspace["id"], "q": "findable"})
    assert default.json() == []

    included = client.get(
        "/search",
        params={"workspace_id": workspace["id"], "q": "findable", "include_archived": True},
    )
    assert [r["node"]["title"] for r in included.json()] == ["Findable"]


def test_search_accepts_special_characters_without_error(client: TestClient) -> None:
    workspace = _workspace(client)

    response = client.get(
        "/search", params={"workspace_id": workspace["id"], "q": 'foo:bar OR NOT ("baz")'}
    )

    assert response.status_code == 200
    assert response.json() == []
