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


def test_dashboard_places_a_fresh_resource_in_inbox_and_unlinked(client: TestClient) -> None:
    workspace = _workspace(client)
    client.post(
        "/resources",
        json={
            "workspace_id": workspace["id"],
            "title": "A paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    )

    response = client.get("/research/dashboard", params={"workspace_id": workspace["id"]})

    assert response.status_code == 200
    body = response.json()
    assert [r["title"] for r in body["inbox"]] == ["A paper"]
    assert [r["title"] for r in body["unlinked"]] == ["A paper"]
    assert body["applied"] == []


def test_get_research_settings_defaults_to_fourteen_days(client: TestClient) -> None:
    workspace = _workspace(client)

    response = client.get("/research-settings", params={"workspace_id": workspace["id"]})

    assert response.status_code == 200
    assert response.json()["stale_after_days"] == 14


def test_update_research_settings_persists(client: TestClient) -> None:
    workspace = _workspace(client)

    response = client.patch(
        "/research-settings",
        params={"workspace_id": workspace["id"]},
        json={"stale_after_days": 30},
    )

    assert response.status_code == 200
    assert response.json()["stale_after_days"] == 30
    assert (
        client.get("/research-settings", params={"workspace_id": workspace["id"]}).json()[
            "stale_after_days"
        ]
        == 30
    )
