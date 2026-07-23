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


def test_request_without_authorization_header_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get("/workspace")
    assert response.status_code == 401


def test_request_with_wrong_token_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get("/workspace", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_request_with_malformed_authorization_header_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    token = app.state.api_token
    response = TestClient(app).get("/workspace", headers={"Authorization": token})
    assert response.status_code == 401


def test_request_with_correct_token_succeeds(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get(
        "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
    )
    assert response.status_code == 200


def test_write_route_also_requires_authentication(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    unauthenticated = TestClient(app)
    workspace_id = unauthenticated.get(
        "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
    ).json()["id"]

    response = unauthenticated.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": "anything", "title": "x"},
    )
    assert response.status_code == 401


def test_api_token_is_generated_once_and_persists_across_app_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "test-workspace.db"
    first_app = create_app(db_path)
    second_app = create_app(db_path)
    assert first_app.state.api_token == second_app.state.api_token


def test_get_workspace_bootstraps_default_schema(client: TestClient) -> None:
    response = client.get("/workspace")
    assert response.status_code == 200
    body = response.json()
    node_type_names = {nt["name"] for nt in body["node_types"]}
    assert {"Task", "Note", "Project", "Decision", "Resource", "Repository"} <= node_type_names


def test_get_workspace_is_stable_across_requests(client: TestClient) -> None:
    first = client.get("/workspace").json()
    second = client.get("/workspace").json()
    assert first["id"] == second["id"]


def _default_workspace_and_task_type(client: TestClient) -> tuple[str, str]:
    workspace = client.get("/workspace").json()
    task_type = next(nt for nt in workspace["node_types"] if nt["name"] == "Task")
    return workspace["id"], task_type["id"]


def test_capture_node_creates_and_lists_it(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)

    response = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Write report"},
    )
    assert response.status_code == 201
    node = response.json()
    assert node["title"] == "Write report"

    listed = client.get("/nodes", params={"workspace_id": workspace_id}).json()
    assert any(n["id"] == node["id"] for n in listed)


def test_capture_node_rejects_unknown_node_type(client: TestClient) -> None:
    workspace_id, _task_type_id = _default_workspace_and_task_type(client)

    response = client.post(
        "/nodes",
        json={
            "workspace_id": workspace_id,
            "node_type_id": "00000000-0000-0000-0000-000000000000",
            "title": "x",
        },
    )
    assert response.status_code == 422


def test_update_node_rejects_blank_title(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Write report"},
    ).json()

    response = client.patch(f"/nodes/{node['id']}", json={"title": "   "})
    assert response.status_code == 422


def test_update_rejects_an_invalid_url_field_value(client: TestClient) -> None:
    """Reproduces the reviewer's own probe: an invalid URL value for a `url`-typed field
    must be rejected, not silently accepted."""
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "reference_url", "field_type": "url"},
    ).json()
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Task 1"},
    ).json()

    response = client.patch(
        f"/nodes/{node['id']}",
        json={"field_values": {field["id"]: "definitely not a URL"}},
    )
    assert response.status_code == 422

    accepted = client.patch(
        f"/nodes/{node['id']}",
        json={"field_values": {field["id"]: "https://example.com/paper"}},
    )
    assert accepted.status_code == 200


def test_update_rejects_a_dangling_object_reference_field_value(client: TestClient) -> None:
    """Reproduces the reviewer's own probe: an `object_reference` value that names no real
    node must be rejected, not silently accepted."""
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "repository", "field_type": "object_reference"},
    ).json()
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Task 1"},
    ).json()

    response = client.patch(
        f"/nodes/{node['id']}",
        json={"field_values": {field["id"]: "not-a-node-id"}},
    )
    assert response.status_code == 422

    target = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Target"},
    ).json()
    accepted = client.patch(
        f"/nodes/{node['id']}",
        json={"field_values": {field["id"]: target["id"]}},
    )
    assert accepted.status_code == 200


def test_converting_a_field_to_object_reference_rejects_a_dangling_existing_value_via_api(
    client: TestClient,
) -> None:
    """Reproduces the reviewer's final probe: setting a text value then converting that
    field's type to `object_reference` must not silently persist a dangling reference."""
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "repository", "field_type": "text"},
    ).json()
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Task 1"},
    ).json()
    set_text_value = client.patch(
        f"/nodes/{node['id']}", json={"field_values": {field["id"]: "not-a-node-id"}}
    )
    assert set_text_value.status_code == 200

    conversion = client.patch(
        f"/node-types/{task_type_id}/fields/{field['id']}",
        json={"workspace_id": workspace_id, "field_type": "object_reference"},
    )
    assert conversion.status_code == 422


def test_update_unknown_node_returns_404(client: TestClient) -> None:
    response = client.patch("/nodes/00000000-0000-0000-0000-000000000000", json={"title": "x"})
    assert response.status_code == 404


def test_archive_node_marks_it_archived(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Write report"},
    ).json()

    response = client.delete(f"/nodes/{node['id']}")
    assert response.status_code == 200
    assert response.json()["is_archived"] is True

    visible = client.get("/nodes", params={"workspace_id": workspace_id}).json()
    assert node["id"] not in {n["id"] for n in visible}


def test_connect_edge_between_two_nodes(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    workspace = client.get("/workspace").json()
    edge_type_id = next(et for et in workspace["edge_types"] if et["name"] == "relates_to")["id"]

    source = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "A"}
    ).json()
    target = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "B"}
    ).json()

    response = client.post(
        "/edges",
        json={
            "workspace_id": workspace_id,
            "edge_type_id": edge_type_id,
            "source_node_id": source["id"],
            "target_node_id": target["id"],
        },
    )
    assert response.status_code == 201
    edge = response.json()

    listed = client.get("/edges", params={"workspace_id": workspace_id}).json()
    assert any(e["id"] == edge["id"] for e in listed)


def test_default_canvas_exists_and_supports_placements(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    canvases = client.get("/canvases", params={"workspace_id": workspace_id}).json()
    assert len(canvases) == 1
    canvas_id = canvases[0]["id"]

    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Write report"},
    ).json()

    placed = client.post(
        f"/canvases/{canvas_id}/placements",
        json={"node_id": node["id"], "position_x": 10.0, "position_y": 20.0},
    )
    assert placed.status_code == 201
    placement = placed.json()

    listed = client.get(f"/canvases/{canvas_id}/placements").json()
    assert any(p["id"] == placement["id"] for p in listed)


def test_update_placement_persists_a_drag(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    canvas_id = client.get("/canvases", params={"workspace_id": workspace_id}).json()[0]["id"]
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Write report"},
    ).json()
    placement = client.post(
        f"/canvases/{canvas_id}/placements",
        json={"node_id": node["id"], "position_x": 0.0, "position_y": 0.0},
    ).json()

    response = client.patch(
        f"/placements/{placement['id']}", json={"position_x": 42.0, "position_y": 84.0}
    )
    assert response.status_code == 200
    moved = response.json()
    assert moved["position_x"] == 42.0
    assert moved["position_y"] == 84.0

    listed = client.get(f"/canvases/{canvas_id}/placements").json()
    reloaded = next(p for p in listed if p["id"] == placement["id"])
    assert reloaded["position_x"] == 42.0


def test_update_unknown_placement_returns_404(client: TestClient) -> None:
    response = client.patch(
        "/placements/00000000-0000-0000-0000-000000000000", json={"position_x": 1.0}
    )
    assert response.status_code == 404


def test_create_node_type_then_add_field_and_status(client: TestClient) -> None:
    workspace_id, _task_type_id = _default_workspace_and_task_type(client)

    created = client.post(
        "/node-types", json={"workspace_id": workspace_id, "name": "Idea", "icon": "lightbulb"}
    )
    assert created.status_code == 201
    node_type = created.json()
    assert node_type["name"] == "Idea"

    field = client.post(
        f"/node-types/{node_type['id']}/fields",
        json={"workspace_id": workspace_id, "name": "priority", "field_type": "text"},
    )
    assert field.status_code == 201
    assert field.json()["name"] == "priority"

    status = client.post(
        f"/node-types/{node_type['id']}/statuses",
        json={"workspace_id": workspace_id, "name": "Open"},
    )
    assert status.status_code == 201
    assert status.json()["name"] == "Open"

    workspace = client.get("/workspace").json()
    reloaded_type = next(nt for nt in workspace["node_types"] if nt["id"] == node_type["id"])
    assert any(f["name"] == "priority" for f in reloaded_type["field_definitions"])
    assert any(s["name"] == "Open" for s in reloaded_type["status_definitions"])


def test_update_field_definition_via_api_succeeds_and_rejects_duplicate_name(
    client: TestClient,
) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    first = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "summary", "field_type": "text"},
    ).json()
    second = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "notes", "field_type": "text"},
    ).json()

    renamed = client.patch(
        f"/node-types/{task_type_id}/fields/{first['id']}",
        json={"workspace_id": workspace_id, "name": "brief"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "brief"

    conflict = client.patch(
        f"/node-types/{task_type_id}/fields/{second['id']}",
        json={"workspace_id": workspace_id, "name": "brief"},
    )
    assert conflict.status_code == 422


def test_changing_a_field_out_of_text_immediately_removes_it_from_search(
    client: TestClient,
) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "notes", "field_type": "text"},
    ).json()
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "A task"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"field_values": {field["id"]: "apischemaprobe"}})
    assert (
        len(
            client.get(
                "/search", params={"workspace_id": workspace_id, "q": "apischemaprobe"}
            ).json()
        )
        == 1
    )

    response = client.patch(
        f"/node-types/{task_type_id}/fields/{field['id']}",
        json={"workspace_id": workspace_id, "field_type": "file_path"},
    )
    assert response.status_code == 200

    assert (
        client.get("/search", params={"workspace_id": workspace_id, "q": "apischemaprobe"}).json()
        == []
    )


def test_update_status_definition_via_api_succeeds_and_rejects_duplicate_name(
    client: TestClient,
) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    first = client.post(
        f"/node-types/{task_type_id}/statuses",
        json={"workspace_id": workspace_id, "name": "Open"},
    ).json()
    second = client.post(
        f"/node-types/{task_type_id}/statuses",
        json={"workspace_id": workspace_id, "name": "Closed"},
    ).json()

    renamed = client.patch(
        f"/node-types/{task_type_id}/statuses/{first['id']}",
        json={"workspace_id": workspace_id, "name": "In Progress"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "In Progress"

    conflict = client.patch(
        f"/node-types/{task_type_id}/statuses/{second['id']}",
        json={"workspace_id": workspace_id, "name": "In Progress"},
    )
    assert conflict.status_code == 422


def test_create_node_type_rejects_duplicate_name(client: TestClient) -> None:
    workspace_id, _task_type_id = _default_workspace_and_task_type(client)

    response = client.post("/node-types", json={"workspace_id": workspace_id, "name": "Task"})
    assert response.status_code == 422


def test_update_node_type_via_api_succeeds_and_rejects_duplicate_name(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    idea = client.post("/node-types", json={"workspace_id": workspace_id, "name": "Idea"}).json()

    renamed = client.patch(
        f"/node-types/{idea['id']}", json={"workspace_id": workspace_id, "name": "Concept"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Concept"

    conflict = client.patch(
        f"/node-types/{task_type_id}", json={"workspace_id": workspace_id, "name": "Concept"}
    )
    assert conflict.status_code == 422


def test_update_node_type_returns_404_for_unknown_node_type(client: TestClient) -> None:
    workspace_id, _task_type_id = _default_workspace_and_task_type(client)

    response = client.patch(
        "/node-types/00000000-0000-0000-0000-000000000000",
        json={"workspace_id": workspace_id, "name": "x"},
    )
    assert response.status_code == 404


def test_title_only_capture_still_succeeds_after_adding_a_required_field(
    client: TestClient,
) -> None:
    """Regression for the reviewer's own reproduction: adding a required field to a node
    type must never dead-end future title-only capture of that type (product principle 6).
    A required field only blocks an *explicit* empty/incompatible value, not an absence."""
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Existing"},
    )

    added = client.post(
        f"/node-types/{task_type_id}/fields",
        json={
            "workspace_id": workspace_id,
            "name": "owner",
            "field_type": "text",
            "is_required": True,
        },
    )
    assert added.status_code == 201

    captured = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "New task"},
    )
    assert captured.status_code == 201

    explicit_none = client.patch(
        f"/nodes/{captured.json()['id']}",
        json={"field_values": {added.json()["id"]: None}},
    )
    assert explicit_none.status_code == 422


def test_remove_field_definition_rejected_when_a_node_still_has_a_value(
    client: TestClient,
) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "custom_note", "field_type": "text"},
    ).json()
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "Task 1"},
    ).json()
    client.patch(f"/nodes/{node['id']}", json={"field_values": {field["id"]: "high"}})

    response = client.delete(
        f"/node-types/{task_type_id}/fields/{field['id']}", params={"workspace_id": workspace_id}
    )
    assert response.status_code == 422


def test_remove_field_definition_succeeds_when_unused(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    field = client.post(
        f"/node-types/{task_type_id}/fields",
        json={"workspace_id": workspace_id, "name": "custom_note", "field_type": "text"},
    ).json()

    response = client.delete(
        f"/node-types/{task_type_id}/fields/{field['id']}", params={"workspace_id": workspace_id}
    )
    assert response.status_code == 204

    workspace = client.get("/workspace").json()
    reloaded_type = next(nt for nt in workspace["node_types"] if nt["id"] == task_type_id)
    assert not any(f["id"] == field["id"] for f in reloaded_type["field_definitions"])


def test_create_and_update_and_remove_edge_type(client: TestClient) -> None:
    workspace_id, _task_type_id = _default_workspace_and_task_type(client)

    created = client.post(
        "/edge-types",
        json={"workspace_id": workspace_id, "name": "assists", "inverse_name": "assisted_by"},
    )
    assert created.status_code == 201
    edge_type = created.json()

    updated = client.patch(
        f"/edge-types/{edge_type['id']}",
        json={"workspace_id": workspace_id, "clear_inverse_name": True},
    )
    assert updated.status_code == 200
    assert updated.json()["inverse_name"] is None

    removed = client.delete(f"/edge-types/{edge_type['id']}", params={"workspace_id": workspace_id})
    assert removed.status_code == 204

    workspace = client.get("/workspace").json()
    assert not any(et["id"] == edge_type["id"] for et in workspace["edge_types"])


def test_remove_edge_type_rejected_when_an_edge_still_uses_it(client: TestClient) -> None:
    workspace_id, task_type_id = _default_workspace_and_task_type(client)
    workspace = client.get("/workspace").json()
    edge_type_id = next(et for et in workspace["edge_types"] if et["name"] == "relates_to")["id"]
    source = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "A"}
    ).json()
    target = client.post(
        "/nodes", json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "B"}
    ).json()
    client.post(
        "/edges",
        json={
            "workspace_id": workspace_id,
            "edge_type_id": edge_type_id,
            "source_node_id": source["id"],
            "target_node_id": target["id"],
        },
    )

    response = client.delete(f"/edge-types/{edge_type_id}", params={"workspace_id": workspace_id})
    assert response.status_code == 422
