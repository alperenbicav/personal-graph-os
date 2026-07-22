from __future__ import annotations

import io
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.api.routers.files import _UPLOAD_READ_CHUNK_BYTES, _iter_upload_chunks

_CURRENT_MACHINE = socket.gethostname()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "test-workspace.db")
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _node_id(client: TestClient) -> str:
    workspace = client.get("/workspace").json()
    task_type_id = next(nt for nt in workspace["node_types"] if nt["name"] == "Task")["id"]
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace["id"], "node_type_id": task_type_id, "title": "A task"},
    ).json()
    return node["id"]


def test_upload_list_download_and_delete_attachment(client: TestClient) -> None:
    node_id = _node_id(client)

    upload = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert upload.status_code == 201
    attachment = upload.json()
    assert attachment["file_name"] == "notes.txt"
    assert attachment["size_bytes"] == len(b"hello world")

    listed = client.get(f"/nodes/{node_id}/attachments").json()
    assert [a["id"] for a in listed] == [attachment["id"]]

    download = client.get(f"/attachments/{attachment['id']}/download")
    assert download.status_code == 200
    assert download.content == b"hello world"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "notes.txt" in download.headers["content-disposition"]

    delete = client.delete(f"/attachments/{attachment['id']}")
    assert delete.status_code == 204
    assert client.get(f"/nodes/{node_id}/attachments").json() == []


def test_iter_upload_chunks_reads_the_spooled_body_lazily_in_bounded_chunks() -> None:
    """Transport-level regression for ST05-F03: proves the route's chunk reader pulls the
    already-received body incrementally instead of materializing it all at once — the
    underlying file's read position only advances as each chunk is actually consumed."""
    payload = b"x" * (_UPLOAD_READ_CHUNK_BYTES * 3)
    upload = SimpleNamespace(file=io.BytesIO(payload))

    chunks = _iter_upload_chunks(upload)  # type: ignore[arg-type]  # duck-typed stub, not a real UploadFile
    assert upload.file.tell() == 0

    first_chunk = next(chunks)
    assert len(first_chunk) == _UPLOAD_READ_CHUNK_BYTES
    assert upload.file.tell() == _UPLOAD_READ_CHUNK_BYTES  # only the first chunk was read

    second_chunk = next(chunks)
    assert len(second_chunk) == _UPLOAD_READ_CHUNK_BYTES
    assert upload.file.tell() == _UPLOAD_READ_CHUNK_BYTES * 2

    remaining = b"".join(chunks)
    assert remaining == b"x" * _UPLOAD_READ_CHUNK_BYTES


def test_upload_attachment_rejects_an_unknown_node(client: TestClient) -> None:
    response = client.post(
        "/nodes/00000000-0000-0000-0000-000000000000/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 404


def test_upload_attachment_over_the_configured_limit_is_413(client: TestClient) -> None:
    node_id = _node_id(client)
    oversized = b"x" * (51 * 1024 * 1024)

    response = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("big.bin", oversized, "application/octet-stream")},
    )

    assert response.status_code == 413
    assert client.get(f"/nodes/{node_id}/attachments").json() == []


def test_download_missing_attachment_is_404(client: TestClient) -> None:
    response = client.get("/attachments/00000000-0000-0000-0000-000000000000/download")
    assert response.status_code == 404


def test_download_reports_404_when_the_managed_file_is_removed_from_disk(
    client: TestClient, tmp_path: Path
) -> None:
    node_id = _node_id(client)
    attachment = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()
    managed_file = tmp_path / "managed-files" / attachment["id"]
    managed_file.unlink()

    response = client.get(f"/attachments/{attachment['id']}/download")

    assert response.status_code == 404


def test_download_reports_409_when_the_managed_file_content_no_longer_matches_its_checksum(
    client: TestClient, tmp_path: Path
) -> None:
    node_id = _node_id(client)
    attachment = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    ).json()
    managed_file = tmp_path / "managed-files" / attachment["id"]
    managed_file.write_bytes(b"HELLO WORLD")  # same length, different bytes

    response = client.get(f"/attachments/{attachment['id']}/download")

    assert response.status_code == 409


def test_file_reference_create_verify_and_delete(client: TestClient, tmp_path: Path) -> None:
    node_id = _node_id(client)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")

    created = client.post(
        f"/nodes/{node_id}/file-references",
        json={
            "machine_name": _CURRENT_MACHINE,
            "relative_path": "source.md",
            "repository_name": "apilex-agent",
            "absolute_path": str(real_file),
        },
    )
    assert created.status_code == 201
    reference = created.json()
    assert reference["is_missing"] is False
    assert reference["last_verified_at"] is None

    verified = client.post(f"/file-references/{reference['id']}/verify")
    assert verified.status_code == 200
    assert verified.json()["is_missing"] is False
    assert verified.json()["last_verified_at"] is not None

    real_file.unlink()
    reverified = client.post(f"/file-references/{reference['id']}/verify")
    assert reverified.json()["is_missing"] is True

    deleted = client.delete(f"/file-references/{reference['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/nodes/{node_id}/file-references").json() == []


def test_verify_without_absolute_path_is_422(client: TestClient) -> None:
    node_id = _node_id(client)
    reference = client.post(
        f"/nodes/{node_id}/file-references",
        json={
            "machine_name": "laptop",
            "relative_path": "repo/README.md",
            "repository_name": "apilex-agent",
        },
    ).json()

    response = client.post(f"/file-references/{reference['id']}/verify")

    assert response.status_code == 422


def test_verify_recorded_for_a_different_machine_is_422(client: TestClient, tmp_path: Path) -> None:
    node_id = _node_id(client)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")
    reference = client.post(
        f"/nodes/{node_id}/file-references",
        json={
            "machine_name": "a-different-machine",
            "relative_path": "source.md",
            "absolute_path": str(real_file),
        },
    ).json()

    response = client.post(f"/file-references/{reference['id']}/verify")

    assert response.status_code == 422


def test_file_reference_rejects_an_unsafe_relative_path(client: TestClient) -> None:
    node_id = _node_id(client)

    response = client.post(
        f"/nodes/{node_id}/file-references",
        json={"machine_name": _CURRENT_MACHINE, "relative_path": "../secret"},
    )

    assert response.status_code == 422


def test_file_reference_create_rejects_an_unknown_node(client: TestClient) -> None:
    response = client.post(
        "/nodes/00000000-0000-0000-0000-000000000000/file-references",
        json={"machine_name": "laptop", "relative_path": "repo/README.md"},
    )
    assert response.status_code == 404


def test_files_routes_require_authentication(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    unauthenticated = TestClient(app)

    response = unauthenticated.get("/nodes/anything/attachments")

    assert response.status_code == 401
