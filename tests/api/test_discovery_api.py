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


def test_preview_classifies_candidates_without_writing_anything(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/preview",
        json={
            "workspace_id": workspace_id,
            "instruction": "find papers on X",
            "candidates": [
                {"identifier": "https://arxiv.org/abs/2401.00001", "title": "A paper"},
                {"identifier": "not a url", "title": "Junk"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [c["decision"] for c in body["candidates"]] == ["create", "reject"]
    assert client.get("/resources", params={"workspace_id": workspace_id}).json() == []


def test_preview_classifies_an_in_batch_duplicate_as_reuse(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/preview",
        json={
            "workspace_id": workspace_id,
            "instruction": "find papers on X",
            "candidates": [
                {"identifier": "https://arxiv.org/abs/2401.00001", "title": "First"},
                {"identifier": "https://arxiv.org/abs/2401.00001v2", "title": "Duplicate"},
            ],
        },
    )

    decisions = [c["decision"] for c in response.json()["candidates"]]
    assert decisions == ["create", "reuse"]


def test_apply_imports_candidates_and_returns_a_completed_discovery_run(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/apply",
        json={
            "workspace_id": workspace_id,
            "agent_identity": "manual-import",
            "instruction": "import a paper and a repo",
            "candidates": [
                {"identifier": "https://arxiv.org/abs/2401.00001", "title": "Paper one"},
                {"identifier": "https://github.com/octocat/hello-world", "title": "A repo"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["completed_at"] is not None
    assert len(body["candidates"]) == 2
    assert all(candidate["outcome"] == "imported" for candidate in body["candidates"])
    resources = client.get("/resources", params={"workspace_id": workspace_id}).json()
    assert len(resources) == 2


def test_reapplying_the_same_candidates_does_not_create_duplicate_resources(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)
    payload = {
        "workspace_id": workspace_id,
        "agent_identity": "manual-import",
        "instruction": "import a paper",
        "candidates": [{"identifier": "https://arxiv.org/abs/2401.00001", "title": "Paper"}],
    }

    client.post("/discovery/apply", json=payload)
    second = client.post("/discovery/apply", json=payload)

    assert second.json()["candidates"][0]["outcome"] == "reused"
    resources = client.get("/resources", params={"workspace_id": workspace_id}).json()
    assert len(resources) == 1


def test_apply_isolates_one_invalid_candidate_from_the_rest_of_the_batch(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/apply",
        json={
            "workspace_id": workspace_id,
            "agent_identity": "manual-import",
            "instruction": "import a mixed batch",
            "candidates": [
                {"identifier": "https://arxiv.org/abs/2401.00001", "title": "Good"},
                {"identifier": "not a url", "title": "Bad"},
            ],
        },
    )

    body = response.json()
    outcomes = {c["title"]: c["outcome"] for c in body["candidates"]}
    assert outcomes == {"Good": "imported", "Bad": "failed"}
    resources = client.get("/resources", params={"workspace_id": workspace_id}).json()
    assert len(resources) == 1


@pytest.mark.parametrize(
    "malformed_url",
    ["https://example.com:bad/path", "https://[bad", "https://example.com:99999/path"],
)
def test_apply_rejects_every_malformed_url_shape_without_a_500(
    client: TestClient, malformed_url: str
) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/apply",
        json={
            "workspace_id": workspace_id,
            "agent_identity": "manual-import",
            "instruction": "import a batch with a malformed URL",
            "candidates": [
                {"identifier": "https://arxiv.org/abs/2401.00001", "title": "Good"},
                {"identifier": malformed_url, "title": "Malformed"},
            ],
        },
    )

    assert response.status_code == 200
    outcomes = {c["title"]: c["outcome"] for c in response.json()["candidates"]}
    assert outcomes == {"Good": "imported", "Malformed": "failed"}
    resources = client.get("/resources", params={"workspace_id": workspace_id}).json()
    assert len(resources) == 1


def test_preview_rejects_an_empty_candidate_batch(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/preview",
        json={"workspace_id": workspace_id, "instruction": "find papers", "candidates": []},
    )

    assert response.status_code == 422


def test_preview_rejects_a_batch_over_the_candidate_limit(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    candidates = [
        {"identifier": f"https://example.com/{i}", "title": f"Item {i}"} for i in range(51)
    ]

    response = client.post(
        "/discovery/preview",
        json={"workspace_id": workspace_id, "instruction": "find papers", "candidates": candidates},
    )

    assert response.status_code == 422


def test_apply_requires_a_non_empty_agent_identity(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    response = client.post(
        "/discovery/apply",
        json={
            "workspace_id": workspace_id,
            "agent_identity": "",
            "instruction": "import",
            "candidates": [{"identifier": "https://arxiv.org/abs/2401.00001", "title": "Paper"}],
        },
    )

    assert response.status_code == 422


def test_preview_raises_404_for_unknown_workspace(client: TestClient) -> None:
    response = client.post(
        "/discovery/preview",
        json={
            "workspace_id": "does-not-exist",
            "instruction": "find papers",
            "candidates": [{"identifier": "https://arxiv.org/abs/2401.00001", "title": "Paper"}],
        },
    )

    assert response.status_code == 404
