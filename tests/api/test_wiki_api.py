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


def test_create_document_persists_metadata_and_first_version(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "My first page",
            "kind": "note",
            "source": "manual",
            "body_markdown": "hello wiki",
            "tag_names": ["personal"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "My first page"
    assert body["kind"] == "note"

    detail = client.get(f"/documents/{body['id']}/detail").json()
    assert detail["latest_version"]["body_markdown"] == "hello wiki"
    assert [tag["name"] for tag in detail["tags"]] == ["personal"]


def test_get_document_detail_returns_404_for_unknown_document(client: TestClient) -> None:
    response = client.get("/documents/does-not-exist/detail")
    assert response.status_code == 404


def test_edit_body_appends_a_new_version(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    document_id = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "Evolving page",
            "kind": "note",
            "source": "manual",
            "body_markdown": "v1",
        },
    ).json()["id"]

    response = client.post(
        f"/documents/{document_id}/versions",
        json={"body_markdown": "v2", "actor": "human/local-user/rest"},
    )

    assert response.status_code == 200
    assert response.json()["version_number"] == 2
    versions = client.get(f"/documents/{document_id}/versions").json()
    assert [v["body_markdown"] for v in versions] == ["v1", "v2"]


def test_backlinks_endpoint_reports_linking_documents(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    target_id = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "Target page",
            "kind": "note",
            "source": "manual",
        },
    ).json()["id"]
    source_id = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "Source page",
            "kind": "note",
            "source": "manual",
        },
    ).json()["id"]

    link_response = client.post(
        f"/documents/{source_id}/links",
        json={"target_type": "document", "target_id": target_id},
    )
    assert link_response.status_code == 200

    backlinks = client.get(f"/documents/{target_id}/backlinks").json()
    assert [doc["id"] for doc in backlinks] == [source_id]


def test_collections_and_tags_are_workspace_scoped(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    collection = client.post(
        "/collections", json={"workspace_id": workspace_id, "name": "Notes"}
    ).json()
    assert collection["name"] == "Notes"

    tag = client.post("/tags", json={"workspace_id": workspace_id, "name": "important"}).json()
    reused_tag = client.post(
        "/tags", json={"workspace_id": workspace_id, "name": "important"}
    ).json()
    assert tag["id"] == reused_tag["id"]

    assert [c["id"] for c in client.get(f"/collections?workspace_id={workspace_id}").json()] == [
        collection["id"]
    ]
    assert [t["id"] for t in client.get(f"/tags?workspace_id={workspace_id}").json()] == [tag["id"]]
