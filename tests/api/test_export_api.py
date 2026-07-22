from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_graph_os.api.app import EXPORT_TEMP_DIR_NAME, create_app


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(tmp_path / "test-workspace.db")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def _task_type_id(client: TestClient) -> str:
    workspace = client.get("/workspace").json()
    return next(nt for nt in workspace["node_types"] if nt["name"] == "Task")["id"]


def test_export_contains_the_fixed_entry_allowlist_and_a_verifiable_manifest(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    )

    response = client.get(f"/export?workspace_id={workspace_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert names == {
        "workspace.json",
        "activity.json",
        "file_references.json",
        "attachments.json",
        "manifest.json",
    }

    manifest = archive.read("manifest.json")
    parsed = json.loads(manifest)
    assert parsed["format_version"] == "1"
    assert parsed["schema_version"] == "1"
    assert parsed["workspace_id"] == workspace_id
    entries_by_path = {entry["path"]: entry for entry in parsed["entries"]}
    for name in names - {"manifest.json"}:
        data = archive.read(name)
        entry = entries_by_path[name]
        assert entry["size_bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()


def test_export_excludes_secrets_and_absolute_paths(client: TestClient) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    ).json()
    client.post(
        f"/nodes/{node['id']}/file-references",
        json={
            "machine_name": "laptop",
            "relative_path": "repo/README.md",
            "repository_name": "repo",
            "absolute_path": "/Users/someone/repo/README.md",
        },
    )

    response = client.get(f"/export?workspace_id={workspace_id}")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert response.headers.get("authorization") is None
    file_references_payload = archive.read("file_references.json").decode("utf-8")
    assert "/Users/someone/repo/README.md" not in file_references_payload
    assert "absolute_path" not in file_references_payload


def test_export_of_a_node_with_a_valid_attachment_includes_verified_bytes(
    client: TestClient,
) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    ).json()
    client.post(
        f"/nodes/{node['id']}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    response = client.get(f"/export?workspace_id={workspace_id}")

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    attachment_entries = [name for name in archive.namelist() if name.startswith("attachments/")]
    assert len(attachment_entries) == 1
    assert archive.read(attachment_entries[0]) == b"hello world"


def test_export_fails_closed_and_leaves_no_temp_file_when_attachment_bytes_are_corrupted(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id = _workspace_id(client)
    task_type_id = _task_type_id(client)
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "x"},
    ).json()
    attachment = client.post(
        f"/nodes/{node['id']}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()

    managed_root = tmp_path / "managed-files"
    (managed_root / attachment["id"]).write_bytes(b"corrupted!!")

    response = client.get(f"/export?workspace_id={workspace_id}")

    assert response.status_code == 409
    export_temp_dir = tmp_path / EXPORT_TEMP_DIR_NAME
    assert not export_temp_dir.exists() or list(export_temp_dir.iterdir()) == []


def test_export_of_an_unknown_workspace_returns_404(client: TestClient) -> None:
    response = client.get("/export?workspace_id=does-not-exist")

    assert response.status_code == 404
