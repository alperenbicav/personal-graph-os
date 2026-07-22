"""ST-05.4: end-to-end provenance acceptance across a simulated process restart.

Exercises the full stack (API -> `FileService` -> SQLite + `LocalManagedFileStore`) the way a
real client would: upload, restart the app against the same DB/managed-root paths, verify exact
bytes/checksum survive, prove a `FileReference` never copies its source, and confirm delete
isolation (removing a reference never touches its source; removing an attachment never touches
its Node).
"""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app

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


def test_upload_survives_a_restart_with_exact_bytes_and_checksum(tmp_path: Path) -> None:
    db_path = tmp_path / "test-workspace.db"
    first_app = create_app(db_path)
    first_client = TestClient(first_app)
    first_client.headers.update({"Authorization": f"Bearer {first_app.state.api_token}"})
    node_id = _node_id(first_client)

    payload = b"a" * (10 * 1024) + b"b" * 512
    upload = first_client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("data.bin", payload, "application/octet-stream")},
    )
    assert upload.status_code == 201
    attachment = upload.json()
    assert attachment["checksum_sha256"] == hashlib.sha256(payload).hexdigest()

    # Simulate a process restart: a fresh app instance against the same database and
    # managed-root path (both derived from `db_path`'s parent), no state carried over in memory.
    second_app = create_app(db_path)
    second_client = TestClient(second_app)
    second_client.headers.update({"Authorization": f"Bearer {second_app.state.api_token}"})

    download = second_client.get(f"/attachments/{attachment['id']}/download")
    assert download.status_code == 200
    assert download.content == payload
    assert hashlib.sha256(download.content).hexdigest() == attachment["checksum_sha256"]

    listed = second_client.get(f"/nodes/{node_id}/attachments").json()
    assert [a["id"] for a in listed] == [attachment["id"]]


def test_file_reference_never_copies_its_source_and_tracks_moved_status(
    client: TestClient, tmp_path: Path
) -> None:
    node_id = _node_id(client)
    source = tmp_path / "external-repo" / "README.md"
    source.parent.mkdir(parents=True)
    source.write_text("original content")

    created = client.post(
        f"/nodes/{node_id}/file-references",
        json={
            "machine_name": _CURRENT_MACHINE,
            "relative_path": "README.md",
            "repository_name": "apilex-agent",
            "absolute_path": str(source),
        },
    ).json()

    verified = client.post(f"/file-references/{created['id']}/verify").json()
    assert verified["is_missing"] is False

    # Prove non-copying: the app's managed root never gains a file with the source's content,
    # and the source itself is untouched by anything the app did.
    managed_root = tmp_path / "managed-files"
    if managed_root.exists():
        copied_bytes = [
            p.read_bytes() for p in managed_root.rglob("*") if p.is_file() and p.name != ".staging"
        ]
        assert b"original content" not in copied_bytes
    assert source.read_text() == "original content"

    source.unlink()
    reverified = client.post(f"/file-references/{created['id']}/verify").json()
    assert reverified["is_missing"] is True


def test_deleting_a_reference_or_attachment_does_not_affect_the_other_or_the_node(
    client: TestClient, tmp_path: Path
) -> None:
    node_id = _node_id(client)
    source = tmp_path / "kept-source.md"
    source.write_text("keep me")

    attachment = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("notes.txt", b"attachment bytes", "text/plain")},
    ).json()
    reference = client.post(
        f"/nodes/{node_id}/file-references",
        json={
            "machine_name": "laptop",
            "relative_path": "kept-source.md",
            "absolute_path": str(source),
        },
    ).json()

    delete_reference = client.delete(f"/file-references/{reference['id']}")
    assert delete_reference.status_code == 204
    assert source.exists()
    assert source.read_text() == "keep me"
    assert client.get(f"/nodes/{node_id}/attachments").json()[0]["id"] == attachment["id"]

    delete_attachment = client.delete(f"/attachments/{attachment['id']}")
    assert delete_attachment.status_code == 204
    node = client.get("/nodes", params={"workspace_id": client.get("/workspace").json()["id"]})
    assert any(n["id"] == node_id for n in node.json())


def test_evidence_pointers_are_stable_and_reusable_by_st06(client: TestClient) -> None:
    node_id = _node_id(client)

    attachment = client.post(
        f"/nodes/{node_id}/attachments",
        files={"file": ("notes.txt", b"data", "text/plain")},
    ).json()
    reference = client.post(
        f"/nodes/{node_id}/file-references",
        json={"machine_name": "laptop", "relative_path": "repo/README.md"},
    ).json()

    assert attachment["evidence_pointer"] == f"attachment:{attachment['id']}"
    assert reference["evidence_pointer"] == f"file-reference:{reference['id']}"

    refetched_attachment = client.get(f"/nodes/{node_id}/attachments").json()[0]
    refetched_reference = client.get(f"/nodes/{node_id}/file-references").json()[0]

    assert refetched_attachment["evidence_pointer"] == attachment["evidence_pointer"]
    assert refetched_reference["evidence_pointer"] == reference["evidence_pointer"]
