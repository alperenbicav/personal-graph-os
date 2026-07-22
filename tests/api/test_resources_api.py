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


def test_create_resource_returns_201_and_combines_node_title(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A great paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "A great paper"
    assert body["kind"] == "paper"
    assert body["canonical_identifier"] == "arxiv:2401.00001"
    assert body["lifecycle_status"] == "inbox"


def test_duplicate_import_returns_200_and_reuses_the_same_resource(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    first = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    ).json()

    second_response = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "Duplicate",
            "raw_source": "https://arxiv.org/abs/2401.00001v3",
        },
    )

    assert second_response.status_code == 200
    assert second_response.json()["id"] == first["id"]


def test_ipv6_resource_canonicalizes_and_replays_idempotently(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "An IPv6-hosted paper",
            "raw_source": "https://[2001:db8::1]:8443/paper",
        },
    )
    assert created.status_code == 201
    canonical_identifier = created.json()["canonical_identifier"]
    assert canonical_identifier == "https://[2001:db8::1]:8443/paper"

    replay = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "Duplicate",
            "raw_source": canonical_identifier,
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]


def test_create_resource_rejects_unknown_workspace(client: TestClient) -> None:
    response = client.post(
        "/resources",
        json={
            "workspace_id": "missing-workspace",
            "title": "x",
            "raw_source": "https://example.com/a",
        },
    )
    assert response.status_code == 404


def test_create_resource_rejects_invalid_raw_source(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    response = client.post(
        "/resources",
        json={"workspace_id": workspace_id, "title": "x", "raw_source": "not a url"},
    )
    assert response.status_code == 422


def test_get_and_list_resources(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()

    get_response = client.get(f"/resources/{created['id']}")
    list_response = client.get("/resources", params={"workspace_id": workspace_id})

    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]
    assert list_response.status_code == 200
    assert [r["id"] for r in list_response.json()] == [created["id"]]


def test_get_unknown_resource_returns_404(client: TestClient) -> None:
    response = client.get("/resources/missing-id")
    assert response.status_code == 404


def test_update_resource_lifecycle_and_pause_dismissal_contract(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()

    rejected = client.patch(f"/resources/{created['id']}", json={"lifecycle_status": "paused"})
    assert rejected.status_code == 422

    accepted = client.patch(
        f"/resources/{created['id']}",
        json={"lifecycle_status": "paused", "next_action_dismissed": True},
    )
    assert accepted.status_code == 200
    assert accepted.json()["lifecycle_status"] == "paused"
    assert accepted.json()["next_action_dismissed"] is True


def test_update_resource_sets_and_clears_progress_percent(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()
    assert created["progress_percent"] is None

    updated = client.patch(f"/resources/{created['id']}", json={"progress_percent": 42})
    assert updated.status_code == 200
    assert updated.json()["progress_percent"] == 42

    cleared = client.patch(f"/resources/{created['id']}", json={"clear_progress_percent": True})
    assert cleared.json()["progress_percent"] is None


def test_update_resource_rejects_a_progress_percent_out_of_bounds(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()

    response = client.patch(f"/resources/{created['id']}", json={"progress_percent": 150})
    assert response.status_code == 422


@pytest.mark.parametrize("value", [True, False, 1.5, "50"])
def test_update_resource_rejects_a_non_integer_progress_percent(
    client: TestClient, value: object
) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()

    response = client.patch(f"/resources/{created['id']}", json={"progress_percent": value})
    assert response.status_code == 422


def test_archive_resource_via_delete_sets_lifecycle_archived(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    created = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://example.com/paper",
        },
    ).json()

    response = client.delete(f"/resources/{created['id']}")

    assert response.status_code == 200
    assert response.json()["lifecycle_status"] == "archived"
