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


def _create_epic(client: TestClient, workspace_id: str) -> dict:
    response = client.post(
        "/work-items",
        json={
            "workspace_id": workspace_id,
            "kind": "epic",
            "work_type": "feature",
            "title": "Agentic OS epic",
            "body": "Epic body",
            "source": "manual",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_list_and_get_work_items(client: TestClient) -> None:
    workspace_id = _workspace_id(client)

    epic = _create_epic(client, workspace_id)
    story = client.post(
        "/work-items",
        json={
            "workspace_id": workspace_id,
            "kind": "story",
            "work_type": "research",
            "title": "A story",
            "source": "manual",
            "parent_id": epic["id"],
            "status": "in_progress",
        },
    ).json()

    listed = client.get(f"/work-items?workspace_id={workspace_id}").json()
    assert {item["id"] for item in listed} == {epic["id"], story["id"]}
    assert epic["title"] == "Agentic OS epic"
    assert epic["body"] == "Epic body"
    assert epic["kind"] == "epic"
    assert epic["parent_id"] is None

    fetched = client.get(f"/work-items/{story['id']}").json()
    assert fetched["parent_id"] == epic["id"]
    assert fetched["status"] == "in_progress"


def test_create_story_rejects_a_task_parent(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task = client.post(
        "/work-items",
        json={
            "workspace_id": workspace_id,
            "kind": "task",
            "work_type": "fix",
            "title": "A task",
            "source": "manual",
        },
    ).json()

    response = client.post(
        "/work-items",
        json={
            "workspace_id": workspace_id,
            "kind": "story",
            "work_type": "feature",
            "title": "Illegal child",
            "source": "manual",
            "parent_id": task["id"],
        },
    )
    assert response.status_code == 422


def test_get_work_item_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/work-items/does-not-exist")
    assert response.status_code == 404


def test_update_work_item_sets_and_clears_planning_fields(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    epic = _create_epic(client, workspace_id)

    updated = client.patch(
        f"/work-items/{epic['id']}",
        json={
            "status": "in_review",
            "priority": "high",
            "assignee": "Ada",
            "due_date": "2026-09-01",
            "progress_percent": 75,
        },
    ).json()
    assert updated["status"] == "in_review"
    assert updated["priority"] == "high"
    assert updated["assignee"] == "Ada"
    assert updated["due_date"] == "2026-09-01"
    assert updated["progress_percent"] == 75

    cleared = client.patch(
        f"/work-items/{epic['id']}",
        json={"clear_priority": True, "clear_due_date": True},
    ).json()
    assert cleared["priority"] is None
    assert cleared["due_date"] is None
    assert cleared["assignee"] == "Ada"


def test_update_work_item_rejects_an_out_of_range_progress(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    epic = _create_epic(client, workspace_id)

    response = client.patch(f"/work-items/{epic['id']}", json={"progress_percent": 101})
    assert response.status_code == 422


def test_checklist_lifecycle_via_the_api(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    epic = _create_epic(client, workspace_id)

    first = client.post(f"/work-items/{epic['id']}/checklist-items", json={"label": "Scope"}).json()
    second = client.post(
        f"/work-items/{epic['id']}/checklist-items", json={"label": "Draft"}
    ).json()
    assert first["position"] == 0
    assert second["position"] == 1

    toggled = client.patch(f"/checklist-items/{first['id']}", json={"is_completed": True}).json()
    assert toggled["is_completed"] is True

    reordered = client.post(
        f"/work-items/{epic['id']}/checklist-items/reorder",
        json={"ordered_ids": [second["id"], first["id"]]},
    ).json()
    assert [item["id"] for item in reordered] == [second["id"], first["id"]]

    client.delete(f"/checklist-items/{second['id']}")
    remaining = client.get(f"/work-items/{epic['id']}/checklist-items").json()
    assert [item["id"] for item in remaining] == [first["id"]]
    assert remaining[0]["position"] == 0


def test_wiki_links_on_a_work_item_via_the_api(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    epic = _create_epic(client, workspace_id)
    document = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "Linked plan",
            "kind": "plan",
            "source": "manual",
        },
    ).json()

    link = client.post(f"/work-items/{epic['id']}/wiki-links", json={"document_id": document["id"]})
    assert link.status_code == 201

    detail = client.get(f"/work-items/{epic['id']}/detail").json()
    assert [doc["document_id"] for doc in detail["linked_documents"]] == [document["id"]]

    client.delete(f"/work-items/{epic['id']}/wiki-links?document_id={document['id']}")
    detail = client.get(f"/work-items/{epic['id']}/detail").json()
    assert detail["linked_documents"] == []


def test_work_item_detail_includes_title_body_and_checklist(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    epic = _create_epic(client, workspace_id)
    client.post(f"/work-items/{epic['id']}/checklist-items", json={"label": "First step"})

    detail = client.get(f"/work-items/{epic['id']}/detail").json()
    assert detail["work_item"]["title"] == "Agentic OS epic"
    assert detail["work_item"]["body"] == "Epic body"
    assert [item["label"] for item in detail["checklist_items"]] == ["First step"]
