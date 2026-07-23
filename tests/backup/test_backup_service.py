from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.backup.service import (
    BackupIntegrityError,
    BackupVersionError,
    PendingFileOperationsError,
    RestoreDestinationNotEmptyError,
    RestoreValidationError,
    create_backup,
    restore_backup,
    verify_backup,
)


def _build_workspace(workspace_dir: Path) -> tuple[FastAPI, TestClient, str, str]:
    app = create_app(workspace_dir / "graph.db")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace = client.get("/workspace").json()
    workspace_id = workspace["id"]
    task_type_id = next(nt for nt in workspace["node_types"] if nt["name"] == "Task")["id"]
    return app, client, workspace_id, task_type_id


def _create_node_with_attachment(client: TestClient, workspace_id: str, task_type_id: str) -> dict:
    node = client.post(
        "/nodes",
        json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "captured"},
    ).json()
    client.post(
        f"/nodes/{node['id']}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    return node


def test_create_backup_produces_a_verifiable_archive(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    node = _create_node_with_attachment(client, workspace_id, task_type_id)
    attachment = client.get(f"/nodes/{node['id']}/attachments").json()[0]
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
    assert "database.sqlite3" in names
    assert "manifest.json" in names
    assert names == {
        "database.sqlite3",
        "manifest.json",
        f"attachments/{attachment['id']}/notes.txt",
    }

    manifest = verify_backup(backup_path)
    assert manifest["format_version"] == "1"
    assert manifest["schema_version"] == "1"
    manifest_entries = manifest["entries"]
    assert isinstance(manifest_entries, list)
    entries_by_path = {entry["path"]: entry for entry in manifest_entries}
    with zipfile.ZipFile(backup_path) as archive:
        for path, entry in entries_by_path.items():
            data = archive.read(path)
            assert entry["size_bytes"] == len(data)


def test_create_backup_excludes_the_bearer_token_and_rebuildable_tables(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")

    with zipfile.ZipFile(backup_path) as archive:
        assert "api-token" not in archive.namelist()
        extracted_dir = tmp_path / "extracted-db"
        extracted_dir.mkdir()
        archive.extract("database.sqlite3", extracted_dir)
    connection = sqlite3.connect(extracted_dir / "database.sqlite3")
    try:
        for table_name in ("search_documents", "idempotency_receipts", "pending_file_operations"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            assert count == 0
        node_count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert node_count == 1
    finally:
        connection.close()


def test_create_backup_refuses_when_pending_file_operations_are_unreconciled(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.execute(
        "INSERT INTO pending_file_operations (id, attachment_id, storage_relative_path, "
        "quarantine_token, created_at) VALUES ('journal-1', 'missing-attachment', 'x', NULL, "
        "datetime('now'))"
    )
    app.state.connection.commit()

    output_dir = tmp_path / "backups"
    with pytest.raises(PendingFileOperationsError):
        create_backup(workspace_dir, output_dir)
    app.state.connection.close()

    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_create_backup_fails_closed_when_an_attachment_is_corrupted(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    node = _create_node_with_attachment(client, workspace_id, task_type_id)
    attachment = client.get(f"/nodes/{node['id']}/attachments").json()[0]
    app.state.connection.close()

    (workspace_dir / "managed-files" / attachment["id"]).write_bytes(b"corrupted!!")

    output_dir = tmp_path / "backups"
    with pytest.raises(Exception, match="no longer matches"):
        create_backup(workspace_dir, output_dir)

    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_verify_rejects_a_tampered_manifest_hash(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")
    tampered_path = tmp_path / "tampered.zip"
    with (
        zipfile.ZipFile(backup_path) as source,
        zipfile.ZipFile(tampered_path, "w", zipfile.ZIP_DEFLATED) as tampered,
    ):
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "database.sqlite3":
                data = data + b"\x00"
            tampered.writestr(info.filename, data)

    with pytest.raises(BackupIntegrityError):
        verify_backup(tampered_path)


def test_verify_rejects_an_unsupported_format_version(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")
    rewritten_path = tmp_path / "rewritten.zip"
    with (
        zipfile.ZipFile(backup_path) as source,
        zipfile.ZipFile(rewritten_path, "w", zipfile.ZIP_DEFLATED) as rewritten,
    ):
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["format_version"] = "999"
                data = json.dumps(manifest).encode("utf-8")
            rewritten.writestr(info.filename, data)

    with pytest.raises(BackupVersionError):
        verify_backup(rewritten_path)


def test_verify_rejects_a_path_traversal_entry(tmp_path: Path) -> None:
    malicious_path = tmp_path / "malicious.zip"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "entries": [{"path": "../evil", "size_bytes": 4, "sha256": "x"}],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../evil", b"evil")
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError):
        verify_backup(malicious_path)


def test_restore_refuses_a_non_empty_destination(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()
    backup_path = create_backup(workspace_dir, tmp_path / "backups")

    destination = tmp_path / "restored"
    destination.mkdir()
    (destination / "pre-existing.txt").write_text("keep me")

    with pytest.raises(RestoreDestinationNotEmptyError):
        restore_backup(backup_path, destination)

    assert (destination / "pre-existing.txt").read_text() == "keep me"


def test_restore_leaves_the_destination_untouched_when_staged_validation_fails(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()
    backup_path = create_backup(workspace_dir, tmp_path / "backups")

    tampered_path = tmp_path / "tampered.zip"
    with (
        zipfile.ZipFile(backup_path) as source,
        zipfile.ZipFile(tampered_path, "w", zipfile.ZIP_DEFLATED) as tampered,
    ):
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("attachments/"):
                data = b"different bytes, same length!!"[: len(data)].ljust(len(data), b"?")
            tampered.writestr(info.filename, data)

    destination = tmp_path / "restored"
    with pytest.raises((BackupIntegrityError, RestoreValidationError)):
        restore_backup(tampered_path, destination)

    assert not destination.exists()
    staging_leftovers = list(tmp_path.glob(".restored.pgos-restore-staging-*"))
    assert staging_leftovers == []


def test_restore_roundtrip_restarts_with_a_new_token_and_preserves_data(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    node = _create_node_with_attachment(client, workspace_id, task_type_id)
    client.patch(f"/nodes/{node['id']}", json={"title": "renamed"})
    original_token = app.state.api_token
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")
    destination = tmp_path / "restored-workspace"
    restore_backup(backup_path, destination)

    assert (destination / "graph.db").exists()
    assert not (destination / "api-token").exists()
    attachment_dirs = list((destination / "managed-files").glob("*"))
    managed_files = [entry for entry in attachment_dirs if entry.is_file()]
    assert len(managed_files) == 1

    restarted_app = create_app(destination / "graph.db")
    restarted_client = TestClient(restarted_app)
    assert restarted_app.state.api_token != original_token
    restarted_client.headers.update({"Authorization": f"Bearer {restarted_app.state.api_token}"})

    restored_nodes = restarted_client.get(f"/nodes?workspace_id={workspace_id}").json()
    restored_node = next(n for n in restored_nodes if n["id"] == node["id"])
    assert restored_node["title"] == "renamed"
    restored_attachments = restarted_client.get(f"/nodes/{node['id']}/attachments").json()
    assert len(restored_attachments) == 1
    downloaded = restarted_client.get(f"/attachments/{restored_attachments[0]['id']}/download")
    assert downloaded.content == b"hello world"

    activity = restarted_client.get(f"/activity-events?workspace_id={workspace_id}").json()
    assert len(activity["events"]) >= 2
    restarted_app.state.connection.close()
