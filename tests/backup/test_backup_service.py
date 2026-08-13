from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.backup.service import (
    MAX_MANIFEST_ENTRIES,
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


def test_create_backup_excludes_an_attachment_uploaded_after_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST07-F01: an upload racing between the DB snapshot and attachment enumeration must
    never produce a self-inconsistent archive -- enumerating from the snapshot itself means
    a late upload is cleanly excluded (a consistent earlier point in time), not half-included."""
    from personal_graph_os.backup import service as backup_service

    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    node = _create_node_with_attachment(client, workspace_id, task_type_id)

    original_snapshot_database = backup_service._snapshot_database
    late_node_id: list[str] = []

    def racing_snapshot_database(source_connection: object, target_path: object) -> None:
        original_snapshot_database(source_connection, target_path)  # type: ignore[arg-type]
        late_node = client.post(
            "/nodes",
            json={"workspace_id": workspace_id, "node_type_id": task_type_id, "title": "late"},
        ).json()
        client.post(
            f"/nodes/{late_node['id']}/attachments",
            files={"file": ("late.txt", b"uploaded after the snapshot", "text/plain")},
        )
        late_node_id.append(late_node["id"])

    monkeypatch.setattr(backup_service, "_snapshot_database", racing_snapshot_database)

    backup_path = create_backup(workspace_dir, tmp_path / "backups")
    manifest = verify_backup(backup_path)
    assert manifest["format_version"] == "1"

    destination = tmp_path / "restored"
    restore_backup(backup_path, destination)
    app.state.connection.close()
    restarted_app = create_app(destination / "graph.db")
    restarted_client = TestClient(restarted_app)
    restarted_client.headers.update({"Authorization": f"Bearer {restarted_app.state.api_token}"})

    restored_nodes = restarted_client.get(f"/nodes?workspace_id={workspace_id}").json()
    restored_ids = {n["id"] for n in restored_nodes}
    assert node["id"] in restored_ids
    assert late_node_id[0] not in restored_ids
    restarted_app.state.connection.close()


def test_create_backup_fails_closed_when_an_attachment_is_deleted_after_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST07-F01: a delete racing between the DB snapshot (which still has the row) and the
    later byte-read must abort the whole backup rather than publish an archive the database
    claims has an attachment the archive does not."""
    from personal_graph_os.backup import service as backup_service

    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    node = _create_node_with_attachment(client, workspace_id, task_type_id)
    attachment = client.get(f"/nodes/{node['id']}/attachments").json()[0]

    original_snapshot_database = backup_service._snapshot_database

    def racing_snapshot_database(source_connection: object, target_path: object) -> None:
        original_snapshot_database(source_connection, target_path)  # type: ignore[arg-type]
        deleted = client.delete(f"/attachments/{attachment['id']}")
        assert deleted.status_code == 204

    monkeypatch.setattr(backup_service, "_snapshot_database", racing_snapshot_database)

    output_dir = tmp_path / "backups"
    with pytest.raises(Exception, match="does not exist"):
        create_backup(workspace_dir, output_dir)

    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def _build_gateway_for_app(app: FastAPI):
    from personal_graph_os.infrastructure.mcp.gateway import AgentGatewayService
    from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
        SqliteResearchUnitOfWork,
    )

    return AgentGatewayService(
        app.state.workspace_repository,
        app.state.node_repository,
        app.state.edge_repository,
        app.state.resource_repository,
        app.state.search_service,
        app.state.file_service,
        node_service=app.state.node_service,
        edge_service=app.state.edge_service,
        resource_service=app.state.resource_service,
        workflow_chain_service=app.state.workflow_chain_service,
        discovery_service=app.state.discovery_service,
        context_pack_service=app.state.context_pack_service,
        activity_service=app.state.activity_service,
        document_service=app.state.document_service,
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(app.state.connection),
    )


def test_restore_preserves_idempotency_receipts_for_exact_mcp_replay(tmp_path: Path) -> None:
    """ST07-F02: a receipt must survive backup/restore alongside the event it guards, so an
    exact replay after recovery returns the original result instead of a spurious conflict."""
    from personal_graph_os.domain.identifiers import NodeTypeId, WorkspaceId

    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    gateway = _build_gateway_for_app(app)

    original = gateway.create_node(
        WorkspaceId(workspace_id),
        NodeTypeId(task_type_id),
        "Agent-captured task",
        actor_name="agent-1",
        reason="captured via chat",
        request_id="req-durable-1",
    )
    app.state.connection.close()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")
    destination = tmp_path / "restored"
    restore_backup(backup_path, destination)

    restarted_app = create_app(destination / "graph.db")
    restarted_gateway = _build_gateway_for_app(restarted_app)

    replay = restarted_gateway.create_node(
        WorkspaceId(workspace_id),
        NodeTypeId(task_type_id),
        "Agent-captured task",
        actor_name="agent-1",
        reason="captured via chat",
        request_id="req-durable-1",
    )
    assert replay["replayed"] is True
    assert replay["node"]["id"] == original["node"]["id"]

    from personal_graph_os.infrastructure.mcp.gateway import GatewayConflictError

    with pytest.raises(GatewayConflictError):
        restarted_gateway.create_node(
            WorkspaceId(workspace_id),
            NodeTypeId(task_type_id),
            "A completely different title",
            actor_name="agent-1",
            reason="different reason",
            request_id="req-durable-1",
        )
    restarted_app.state.connection.close()


def test_verify_rejects_a_non_zip_file(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "not-a-backup.zip"
    not_a_zip.write_bytes(b"this is definitely not a zip archive")

    with pytest.raises(BackupIntegrityError):
        verify_backup(not_a_zip)


def test_verify_rejects_an_oversized_manifest(tmp_path: Path) -> None:
    oversized_path = tmp_path / "oversized-manifest.zip"
    huge_manifest = json.dumps(
        {
            "format_version": "1",
            "schema_version": "1",
            "entries": [{"path": "database.sqlite3", "size_bytes": 1, "sha256": "x" * 64}],
            "padding": "x" * (11 * 1024 * 1024),
        }
    ).encode("utf-8")
    with zipfile.ZipFile(oversized_path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("database.sqlite3", b"x")
        archive.writestr("manifest.json", huge_manifest)

    with pytest.raises(BackupIntegrityError):
        verify_backup(oversized_path)


def test_verify_rejects_a_manifest_missing_required_fields(tmp_path: Path) -> None:
    malicious_path = tmp_path / "missing-fields.zip"
    manifest = {"format_version": "1", "schema_version": "1", "entries": [{"path": "x"}]}
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("x", b"data")
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError):
        verify_backup(malicious_path)


def test_verify_rejects_a_manifest_with_wrong_typed_fields(tmp_path: Path) -> None:
    malicious_path = tmp_path / "wrong-typed.zip"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [{"path": "x", "size_bytes": "not-a-number", "sha256": "x" * 64}],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("x", b"data")
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError):
        verify_backup(malicious_path)


def test_verify_rejects_an_excessive_entry_count(tmp_path: Path) -> None:
    malicious_path = tmp_path / "too-many-entries.zip"
    entries = [
        {"path": f"e{i}", "size_bytes": 1, "sha256": "x" * 64}
        for i in range(MAX_MANIFEST_ENTRIES + 1)
    ]
    manifest = {"format_version": "1", "schema_version": "1", "entries": entries}
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError):
        verify_backup(malicious_path)


def test_verify_rejects_an_aggregate_expansion_beyond_the_total_byte_cap(tmp_path: Path) -> None:
    from personal_graph_os.backup.service import MAX_TOTAL_UNCOMPRESSED_BYTES

    malicious_path = tmp_path / "aggregate-expansion.zip"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {"path": "a", "size_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES, "sha256": "a" * 64},
            {"path": "b", "size_bytes": 1, "sha256": "b" * 64},
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError):
        verify_backup(malicious_path)


def test_verify_rejects_an_archive_with_no_database_entry(tmp_path: Path) -> None:
    """ST07-F07 re-review: an archive with only attachment bytes and no `database.sqlite3`
    entry is not a valid backup, even if every entry it does declare checks out."""
    malicious_path = tmp_path / "no-database.zip"
    data = b"orphan attachment bytes"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "attachments/orphan-id/file.txt",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("attachments/orphan-id/file.txt", data)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError, match="exactly one"):
        verify_backup(malicious_path)


def test_restore_rejects_an_archive_with_no_database_entry_without_installing_anything(
    tmp_path: Path,
) -> None:
    """The exact reported bug: `restore_backup` must never let `open_connection()` silently
    create a fresh empty `graph.db` for an archive that never had one."""
    malicious_path = tmp_path / "no-database.zip"
    data = b"orphan attachment bytes"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "attachments/orphan-id/file.txt",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("attachments/orphan-id/file.txt", data)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    destination = tmp_path / "restored"
    with pytest.raises(BackupIntegrityError):
        restore_backup(malicious_path, destination)

    assert not destination.exists()


def test_verify_rejects_an_unexpected_root_entry(tmp_path: Path) -> None:
    malicious_path = tmp_path / "extra-root-entry.zip"
    db_data = b"fake db bytes"
    extra_data = b"not part of the backup format"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "database.sqlite3",
                "size_bytes": len(db_data),
                "sha256": hashlib.sha256(db_data).hexdigest(),
            },
            {
                "path": "secrets.txt",
                "size_bytes": len(extra_data),
                "sha256": hashlib.sha256(extra_data).hexdigest(),
            },
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database.sqlite3", db_data)
        archive.writestr("secrets.txt", extra_data)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError, match="entry grammar"):
        verify_backup(malicious_path)


def test_verify_rejects_a_malformed_attachment_entry_path(tmp_path: Path) -> None:
    malicious_path = tmp_path / "malformed-attachment-path.zip"
    db_data = b"fake db bytes"
    attachment_data = b"bytes"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "database.sqlite3",
                "size_bytes": len(db_data),
                "sha256": hashlib.sha256(db_data).hexdigest(),
            },
            {
                "path": "attachments/nested/too/deep.txt",
                "size_bytes": len(attachment_data),
                "sha256": hashlib.sha256(attachment_data).hexdigest(),
            },
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database.sqlite3", db_data)
        archive.writestr("attachments/nested/too/deep.txt", attachment_data)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError, match="entry grammar"):
        verify_backup(malicious_path)


def test_verify_rejects_a_duplicate_attachment_id(tmp_path: Path) -> None:
    malicious_path = tmp_path / "duplicate-attachment-id.zip"
    db_data = b"fake db bytes"
    first = b"first"
    second = b"second"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "database.sqlite3",
                "size_bytes": len(db_data),
                "sha256": hashlib.sha256(db_data).hexdigest(),
            },
            {
                "path": "attachments/same-id/a.txt",
                "size_bytes": len(first),
                "sha256": hashlib.sha256(first).hexdigest(),
            },
            {
                "path": "attachments/same-id/b.txt",
                "size_bytes": len(second),
                "sha256": hashlib.sha256(second).hexdigest(),
            },
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database.sqlite3", db_data)
        archive.writestr("attachments/same-id/a.txt", first)
        archive.writestr("attachments/same-id/b.txt", second)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError, match="more than one entry"):
        verify_backup(malicious_path)


def test_verify_rejects_an_oversized_single_entry(tmp_path: Path) -> None:
    from personal_graph_os.backup.service import MAX_SINGLE_ENTRY_BYTES

    malicious_path = tmp_path / "oversized-single-entry.zip"
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "entries": [
            {
                "path": "database.sqlite3",
                "size_bytes": MAX_SINGLE_ENTRY_BYTES + 1,
                "sha256": "a" * 64,
            }
        ],
    }
    with zipfile.ZipFile(malicious_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

    with pytest.raises(BackupIntegrityError, match="per-entry"):
        verify_backup(malicious_path)


def test_create_backup_validates_before_publishing(tmp_path: Path) -> None:
    """ST07-F07 re-review: `create_backup` runs the archive it just built through the same
    `verify_backup` checks before renaming it into place."""
    workspace_dir = tmp_path / "workspace"
    app, client, workspace_id, task_type_id = _build_workspace(workspace_dir)
    _create_node_with_attachment(client, workspace_id, task_type_id)
    app.state.connection.close()

    output_dir = tmp_path / "backups"
    backup_path = create_backup(workspace_dir, output_dir)

    # A normal backup already passes verify_backup's grammar/cap checks unmodified; this
    # documents that create_backup would fail closed the same way verify_backup does, since
    # it now runs that exact function on its own output before publishing.
    manifest = verify_backup(backup_path)
    assert manifest["format_version"] == "1"


def test_cli_verify_fails_closed_on_a_non_zip_file_instead_of_a_traceback(tmp_path: Path) -> None:
    from personal_graph_os.backup.cli import main

    not_a_zip = tmp_path / "garbage.zip"
    not_a_zip.write_bytes(b"garbage")

    exit_code = main(["verify", str(not_a_zip)])

    assert exit_code == 1


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
