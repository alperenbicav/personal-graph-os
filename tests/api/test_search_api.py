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
    assert [r["node"]["title"] for r in body["results"]] == ["Attention Is All You Need"]


def test_search_excludes_archived_nodes_unless_requested(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": node_type_id, "title": "Findable"},
    ).json()
    client.delete(f"/nodes/{node['id']}")

    default = client.get("/search", params={"workspace_id": workspace["id"], "q": "findable"})
    assert default.json()["results"] == []

    included = client.get(
        "/search",
        params={"workspace_id": workspace["id"], "q": "findable", "include_archived": True},
    )
    assert [r["node"]["title"] for r in included.json()["results"]] == ["Findable"]


def test_search_accepts_special_characters_without_error(client: TestClient) -> None:
    workspace = _workspace(client)

    response = client.get(
        "/search", params={"workspace_id": workspace["id"], "q": 'foo:bar OR NOT ("baz")'}
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_search_scope_filters_the_result_set(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    client.post(
        "/nodes",
        json={
            "workspace_id": workspace["id"],
            "node_type_id": node_type_id,
            "title": "graphonly generic",
        },
    )
    client.post(
        "/documents",
        json={
            "workspace_id": workspace["id"],
            "title": "wikionly document",
            "kind": "note",
            "source": "manual",
            "body_markdown": "a wikionly body",
        },
    )

    graph_response = client.get(
        "/search",
        params={"workspace_id": workspace["id"], "q": "graphonly", "scope": "graph"},
    )
    wiki_response = client.get(
        "/search",
        params={"workspace_id": workspace["id"], "q": "wikionly", "scope": "wiki"},
    )
    graph_scope_miss = client.get(
        "/search",
        params={"workspace_id": workspace["id"], "q": "wikionly", "scope": "graph"},
    )

    assert graph_response.status_code == 200
    assert [r["node"]["title"] for r in graph_response.json()["results"]] == ["graphonly generic"]
    assert wiki_response.status_code == 200
    wiki_body = wiki_response.json()["results"]
    assert len(wiki_body) == 1
    assert wiki_body[0]["node"] is None
    assert wiki_body[0]["document"]["title"] == "wikionly document"
    assert wiki_body[0]["scope"] == "wiki"
    assert wiki_body[0]["goto"] == wiki_body[0]["document"]["id"]
    assert graph_scope_miss.json()["results"] == []


def test_search_returns_score_and_source_projection(client: TestClient) -> None:
    workspace = _workspace(client)
    node_type_id = workspace["node_types"][0]["id"]
    client.post(
        "/nodes",
        json={
            "workspace_id": workspace["id"],
            "node_type_id": node_type_id,
            "title": "scoreprobe title",
        },
    )

    response = client.get("/search", params={"workspace_id": workspace["id"], "q": "scoreprobe"})

    assert response.status_code == 200
    body = response.json()["results"]
    assert len(body) == 1
    assert "score" in body[0]
    assert isinstance(body[0]["score"], float)
    assert body[0]["entity_type"] == "node"
    assert body[0]["goto"] == body[0]["node"]["id"]
