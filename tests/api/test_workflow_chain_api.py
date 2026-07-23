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


def test_advance_creates_a_takeaway_node_and_returns_it_with_the_edge(client: TestClient) -> None:
    workspace = _workspace(client)
    resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace["id"],
            "title": "A paper",
            "raw_source": "https://arxiv.org/abs/2401.00002",
        },
    ).json()

    response = client.post(
        "/workflow-chain/advance",
        json={
            "workspace_id": workspace["id"],
            "source_node_id": resource["node_id"],
            "step": "resource_to_takeaway",
            "title": "Key insight",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["node"]["title"] == "Key insight"
    assert body["edge"]["source_node_id"] == resource["node_id"]
    assert body["edge"]["target_node_id"] == body["node"]["id"]


def test_advance_connects_to_an_existing_target_instead_of_creating_one(
    client: TestClient,
) -> None:
    workspace = _workspace(client)
    resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace["id"],
            "title": "A paper",
            "raw_source": "https://arxiv.org/abs/2401.00003",
        },
    ).json()
    first_advance = client.post(
        "/workflow-chain/advance",
        json={
            "workspace_id": workspace["id"],
            "source_node_id": resource["node_id"],
            "step": "resource_to_takeaway",
            "title": "Existing takeaway",
        },
    ).json()
    existing_takeaway_id = first_advance["node"]["id"]

    other_resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace["id"],
            "title": "Another paper",
            "raw_source": "https://arxiv.org/abs/2401.00004",
        },
    ).json()

    response = client.post(
        "/workflow-chain/advance",
        json={
            "workspace_id": workspace["id"],
            "source_node_id": other_resource["node_id"],
            "step": "resource_to_takeaway",
            "existing_target_node_id": existing_takeaway_id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["node"]["id"] == existing_takeaway_id
    assert body["edge"]["source_node_id"] == other_resource["node_id"]
    assert body["edge"]["target_node_id"] == existing_takeaway_id


def test_advance_rejects_a_source_node_of_the_wrong_role(client: TestClient) -> None:
    workspace = _workspace(client)
    non_resource_type = next(
        nt for nt in workspace["node_types"] if nt.get("system_key") != "resource"
    )
    ordinary_node = client.post(
        "/nodes",
        json={
            "workspace_id": workspace["id"],
            "node_type_id": non_resource_type["id"],
            "title": "Not a resource",
        },
    ).json()

    response = client.post(
        "/workflow-chain/advance",
        json={
            "workspace_id": workspace["id"],
            "source_node_id": ordinary_node["id"],
            "step": "resource_to_takeaway",
            "title": "x",
        },
    )

    assert response.status_code == 422
