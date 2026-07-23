from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from personal_graph_os.application.file_service import (
    AttachmentNotFoundError,
    FileReferenceNotFoundError,
    FileService,
    NodeNotFoundError,
    UnverifiableFileReferenceError,
)
from personal_graph_os.domain.errors import InvariantViolationError, UploadTooLargeError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import AttachmentId, NodeId, new_id
from personal_graph_os.domain.schema import NodeType, Workspace
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteAttachmentRepository,
    SqliteFileReferenceRepository,
    SqliteNodeRepository,
    SqlitePendingFileOperationRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _service_with_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> tuple[FileService, NodeId]:
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)

    service = FileService(
        SqliteNodeRepository(sqlite_connection),
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        LocalManagedFileStore(tmp_path / "managed-root"),
        SqlitePendingFileOperationRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        max_upload_bytes=1024,
        current_machine_name=lambda: "laptop",
    )
    return service, node.id


def test_upload_download_and_delete_attachment_round_trip(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    attachment = service.upload_attachment(
        node_id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello world"]
    )

    assert attachment in service.list_attachments(node_id)
    reloaded, stream = service.download_attachment(attachment.id)
    with stream:
        assert stream.read() == b"hello world"
    assert reloaded.checksum_sha256 == attachment.checksum_sha256

    service.delete_attachment(attachment.id)
    assert service.list_attachments(node_id) == ()
    with pytest.raises(AttachmentNotFoundError):
        service.download_attachment(attachment.id)


def test_upload_attachment_rejects_an_unknown_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, _ = _service_with_node(sqlite_connection, tmp_path)

    with pytest.raises(NodeNotFoundError):
        service.upload_attachment(
            NodeId(new_id()), file_name="notes.pdf", mime_type="application/pdf", chunks=[b"x"]
        )


def test_upload_attachment_over_the_limit_leaves_no_row_or_orphan_file(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    with pytest.raises(UploadTooLargeError):
        service.upload_attachment(
            node_id, file_name="big.bin", mime_type="application/octet-stream", chunks=[b"x" * 2000]
        )

    assert service.list_attachments(node_id) == ()
    managed_root = tmp_path / "managed-root"
    assert list((managed_root / ".staging").iterdir()) == []
    assert list((managed_root / ".trash").iterdir()) == []
    assert [p for p in managed_root.iterdir() if p.name not in (".staging", ".trash")] == []


def test_delete_attachment_rejects_an_unknown_id(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, _ = _service_with_node(sqlite_connection, tmp_path)

    with pytest.raises(AttachmentNotFoundError):
        service.delete_attachment(AttachmentId(new_id()))


def test_file_reference_create_list_and_delete(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    reference = service.create_file_reference(
        node_id,
        machine_name="laptop",
        relative_path="repo/README.md",
        repository_name="apilex-agent",
    )

    assert reference in service.list_file_references(node_id)

    service.delete_file_reference(reference.id)
    assert service.list_file_references(node_id) == ()
    with pytest.raises(FileReferenceNotFoundError):
        service.delete_file_reference(reference.id)


def test_file_reference_create_rejects_an_unknown_node(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, _ = _service_with_node(sqlite_connection, tmp_path)

    with pytest.raises(NodeNotFoundError):
        service.create_file_reference(
            NodeId(new_id()), machine_name="laptop", relative_path="repo/README.md"
        )


def test_verify_file_reference_marks_an_existing_absolute_path_present(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")
    reference = service.create_file_reference(
        node_id, machine_name="laptop", relative_path="source.md", absolute_path=str(real_file)
    )

    verified = service.verify_file_reference(reference.id)

    assert verified.is_missing is False
    assert verified.last_verified_at is not None


def test_verify_file_reference_marks_a_moved_source_missing(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    reference = service.create_file_reference(
        node_id,
        machine_name="laptop",
        relative_path="gone.md",
        absolute_path=str(tmp_path / "gone.md"),
    )

    verified = service.verify_file_reference(reference.id)

    assert verified.is_missing is True


def test_verify_file_reference_without_an_absolute_path_is_explicitly_unverifiable(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    reference = service.create_file_reference(
        node_id,
        machine_name="laptop",
        relative_path="repo/README.md",
        repository_name="apilex-agent",
    )

    with pytest.raises(UnverifiableFileReferenceError):
        service.verify_file_reference(reference.id)

    unchanged = service.list_file_references(node_id)[0]
    assert unchanged.last_verified_at is None
    assert unchanged.is_missing is False


def test_verify_file_reference_recorded_for_a_different_machine_is_unverifiable(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")
    reference = service.create_file_reference(
        node_id,
        machine_name="a-different-machine",
        relative_path="source.md",
        absolute_path=str(real_file),
    )

    with pytest.raises(UnverifiableFileReferenceError, match="a-different-machine"):
        service.verify_file_reference(reference.id)

    unchanged = service.list_file_references(node_id)[0]
    assert unchanged.last_verified_at is None


def test_verify_file_reference_treats_a_symlinked_path_as_missing(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")
    link = tmp_path / "source-link.md"
    link.symlink_to(real_file)
    reference = service.create_file_reference(
        node_id, machine_name="laptop", relative_path="source-link.md", absolute_path=str(link)
    )

    verified = service.verify_file_reference(reference.id)

    assert verified.is_missing is True


def test_upload_attachment_rejects_an_unsafe_file_name_before_any_store_write(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    with pytest.raises(InvariantViolationError):
        service.upload_attachment(
            node_id, file_name="../unsafe.txt", mime_type="text/plain", chunks=[b"data"]
        )

    managed_root = tmp_path / "managed-root"
    assert (
        not managed_root.exists()
        or [p for p in managed_root.iterdir() if p.name not in (".staging", ".trash")] == []
    )
    assert service.list_attachments(node_id) == ()


class _AttachmentRepositoryDeleteFailsOnce:
    """Wraps a real repository but makes `delete()` fail exactly once, to prove a repository
    failure after quarantine restores the file so the attachment stays fully intact/retryable."""

    def __init__(self, wrapped: SqliteAttachmentRepository) -> None:
        self._wrapped = wrapped
        self.delete_calls = 0

    def get(self, attachment_id):
        return self._wrapped.get(attachment_id)

    def list_by_node(self, node_id):
        return self._wrapped.list_by_node(node_id)

    def save(self, attachment):
        self._wrapped.save(attachment)

    def save_without_commit(self, attachment):
        self._wrapped.save_without_commit(attachment)

    def delete(self, attachment_id):
        self.delete_calls += 1
        if self.delete_calls == 1:
            raise sqlite3.OperationalError("simulated repository failure")
        self._wrapped.delete(attachment_id)

    def delete_without_commit(self, attachment_id):
        self.delete_calls += 1
        if self.delete_calls == 1:
            raise sqlite3.OperationalError("simulated repository failure")
        self._wrapped.delete_without_commit(attachment_id)


def test_delete_attachment_restores_bytes_when_the_repository_delete_fails(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    flaky_repository = _AttachmentRepositoryDeleteFailsOnce(
        SqliteAttachmentRepository(sqlite_connection)
    )
    service = FileService(
        SqliteNodeRepository(sqlite_connection),
        flaky_repository,
        SqliteFileReferenceRepository(sqlite_connection),
        LocalManagedFileStore(tmp_path / "managed-root"),
        SqlitePendingFileOperationRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)
    attachment = service.upload_attachment(
        node.id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello world"]
    )

    with pytest.raises(sqlite3.OperationalError):
        service.delete_attachment(attachment.id)

    # Row and bytes must both still be present and consistent: the quarantined file was
    # restored, so the attachment is exactly as it was before the failed delete attempt.
    assert attachment in service.list_attachments(node.id)
    _, stream = service.download_attachment(attachment.id)
    with stream:
        assert stream.read() == b"hello world"

    service.delete_attachment(attachment.id)
    assert service.list_attachments(node.id) == ()


class _AttachmentRepositoryDeleteAlwaysFails:
    def __init__(self, wrapped: SqliteAttachmentRepository) -> None:
        self._wrapped = wrapped

    def get(self, attachment_id):
        return self._wrapped.get(attachment_id)

    def list_by_node(self, node_id):
        return self._wrapped.list_by_node(node_id)

    def save(self, attachment):
        self._wrapped.save(attachment)

    def save_without_commit(self, attachment):
        self._wrapped.save_without_commit(attachment)

    def delete(self, attachment_id):
        raise sqlite3.OperationalError("simulated repository delete failure")

    def delete_without_commit(self, attachment_id):
        raise sqlite3.OperationalError("simulated repository delete failure")


class _StoreRestoreAlwaysFails:
    def __init__(self, wrapped: LocalManagedFileStore) -> None:
        self._wrapped = wrapped

    def receive_upload(self, chunks, *, max_size_bytes):
        return self._wrapped.receive_upload(chunks, max_size_bytes=max_size_bytes)

    def finalize(self, uploaded, attachment_id):
        return self._wrapped.finalize(uploaded, attachment_id)

    def discard(self, uploaded):
        self._wrapped.discard(uploaded)

    def open_read(self, storage_relative_path):
        return self._wrapped.open_read(storage_relative_path)

    def open_verified(
        self, storage_relative_path, *, expected_size_bytes, expected_checksum_sha256
    ):
        return self._wrapped.open_verified(
            storage_relative_path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum_sha256=expected_checksum_sha256,
        )

    def delete(self, storage_relative_path):
        self._wrapped.delete(storage_relative_path)

    def quarantine(self, storage_relative_path):
        return self._wrapped.quarantine(storage_relative_path)

    def restore(self, quarantine_token, storage_relative_path):
        raise OSError("simulated restore failure")

    def purge_quarantined(self, quarantine_token):
        self._wrapped.purge_quarantined(quarantine_token)


def test_delete_attachment_journals_and_recovers_when_both_the_repository_delete_and_restore_fail(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    real_store = LocalManagedFileStore(tmp_path / "managed-root")
    pending_operations = SqlitePendingFileOperationRepository(sqlite_connection)
    service = FileService(
        SqliteNodeRepository(sqlite_connection),
        _AttachmentRepositoryDeleteAlwaysFails(SqliteAttachmentRepository(sqlite_connection)),
        SqliteFileReferenceRepository(sqlite_connection),
        _StoreRestoreAlwaysFails(real_store),
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)
    attachment = service.upload_attachment(
        node.id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello world"]
    )

    with pytest.raises(sqlite3.OperationalError, match="simulated repository delete failure"):
        service.delete_attachment(attachment.id)

    # The row still exists (delete failed) but its bytes sit in quarantine (restore also
    # failed) — the journal entry is the only record of that, and must survive so
    # reconciliation can put the file back where the still-live row expects it.
    (entry,) = pending_operations.list_all()
    assert entry.quarantine_token is not None
    managed_root = tmp_path / "managed-root"
    assert not (managed_root / attachment.storage_relative_path).exists()

    recovering_service = FileService(
        SqliteNodeRepository(sqlite_connection),
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        real_store,
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    recovering_service.reconcile_pending_operations()

    assert pending_operations.list_all() == ()
    assert (managed_root / attachment.storage_relative_path).exists()
    _, stream = recovering_service.download_attachment(attachment.id)
    with stream:
        assert stream.read() == b"hello world"


class _AttachmentRepositorySaveAlwaysFails:
    """Simulates a repository whose `save()` always fails, to prove that when the upload
    compensation delete *also* fails, the caller still sees the original save failure rather
    than having it masked by the compensation error."""

    def __init__(self, wrapped: SqliteAttachmentRepository) -> None:
        self._wrapped = wrapped

    def get(self, attachment_id):
        return self._wrapped.get(attachment_id)

    def list_by_node(self, node_id):
        return self._wrapped.list_by_node(node_id)

    def save(self, attachment):
        raise sqlite3.OperationalError("simulated repository save failure")

    def save_without_commit(self, attachment):
        raise sqlite3.OperationalError("simulated repository save failure")

    def delete(self, attachment_id):
        self._wrapped.delete(attachment_id)

    def delete_without_commit(self, attachment_id):
        self._wrapped.delete_without_commit(attachment_id)


class _StoreDeleteAlwaysFails:
    def __init__(self, wrapped: LocalManagedFileStore) -> None:
        self._wrapped = wrapped

    def receive_upload(self, chunks, *, max_size_bytes):
        return self._wrapped.receive_upload(chunks, max_size_bytes=max_size_bytes)

    def finalize(self, uploaded, attachment_id):
        return self._wrapped.finalize(uploaded, attachment_id)

    def discard(self, uploaded):
        self._wrapped.discard(uploaded)

    def open_read(self, storage_relative_path):
        return self._wrapped.open_read(storage_relative_path)

    def open_verified(
        self, storage_relative_path, *, expected_size_bytes, expected_checksum_sha256
    ):
        return self._wrapped.open_verified(
            storage_relative_path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum_sha256=expected_checksum_sha256,
        )

    def delete(self, storage_relative_path):
        raise OSError("simulated compensation-delete failure")

    def quarantine(self, storage_relative_path):
        return self._wrapped.quarantine(storage_relative_path)

    def restore(self, quarantine_token, storage_relative_path):
        self._wrapped.restore(quarantine_token, storage_relative_path)

    def purge_quarantined(self, quarantine_token):
        self._wrapped.purge_quarantined(quarantine_token)


def test_upload_attachment_preserves_the_error_and_journals_the_orphan_when_compensation_fails(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    real_store = LocalManagedFileStore(tmp_path / "managed-root")
    pending_operations = SqlitePendingFileOperationRepository(sqlite_connection)
    service = FileService(
        SqliteNodeRepository(sqlite_connection),
        _AttachmentRepositorySaveAlwaysFails(SqliteAttachmentRepository(sqlite_connection)),
        SqliteFileReferenceRepository(sqlite_connection),
        _StoreDeleteAlwaysFails(real_store),
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)

    with pytest.raises(sqlite3.OperationalError, match="simulated repository save failure"):
        service.upload_attachment(
            node.id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello world"]
        )

    # The compensation delete failed too, so the finalized file is a real orphan on disk —
    # but the journal entry survives, so reconciliation (e.g. on the next app startup) can
    # still clean it up rather than it being silently lost.
    (entry,) = pending_operations.list_all()
    assert entry.quarantine_token is None
    managed_root = tmp_path / "managed-root"
    assert (managed_root / entry.storage_relative_path).exists()

    recovering_service = FileService(
        SqliteNodeRepository(sqlite_connection),
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        real_store,
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    recovering_service.reconcile_pending_operations()

    assert pending_operations.list_all() == ()
    assert not (managed_root / entry.storage_relative_path).exists()


class _StorePurgeQuarantinedFailsOnce:
    """Wraps a real store but makes `purge_quarantined()` fail exactly once, to prove that a
    row delete which already succeeded is not undone by a later, harmless cleanup failure."""

    def __init__(self, wrapped: LocalManagedFileStore) -> None:
        self._wrapped = wrapped
        self.purge_calls = 0

    def receive_upload(self, chunks, *, max_size_bytes):
        return self._wrapped.receive_upload(chunks, max_size_bytes=max_size_bytes)

    def finalize(self, uploaded, attachment_id):
        return self._wrapped.finalize(uploaded, attachment_id)

    def discard(self, uploaded):
        self._wrapped.discard(uploaded)

    def open_read(self, storage_relative_path):
        return self._wrapped.open_read(storage_relative_path)

    def open_verified(
        self, storage_relative_path, *, expected_size_bytes, expected_checksum_sha256
    ):
        return self._wrapped.open_verified(
            storage_relative_path,
            expected_size_bytes=expected_size_bytes,
            expected_checksum_sha256=expected_checksum_sha256,
        )

    def delete(self, storage_relative_path):
        self._wrapped.delete(storage_relative_path)

    def quarantine(self, storage_relative_path):
        return self._wrapped.quarantine(storage_relative_path)

    def restore(self, quarantine_token, storage_relative_path):
        self._wrapped.restore(quarantine_token, storage_relative_path)

    def purge_quarantined(self, quarantine_token):
        self.purge_calls += 1
        if self.purge_calls == 1:
            raise OSError("simulated purge failure")
        self._wrapped.purge_quarantined(quarantine_token)


def test_delete_attachment_succeeds_even_when_purging_the_quarantined_file_fails(
    sqlite_connection: sqlite3.Connection, tmp_path: Path
) -> None:
    real_store = LocalManagedFileStore(tmp_path / "managed-root")
    flaky_store = _StorePurgeQuarantinedFailsOnce(real_store)
    pending_operations = SqlitePendingFileOperationRepository(sqlite_connection)
    service = FileService(
        SqliteNodeRepository(sqlite_connection),
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        flaky_store,
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    node_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(node_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node = Node(workspace_id=workspace.id, node_type_id=node_type.id, title="Task 1")
    SqliteNodeRepository(sqlite_connection).save(node)
    attachment = service.upload_attachment(
        node.id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello world"]
    )

    # Does not raise: the repository row is already gone, so the attachment is correctly
    # deleted even though the quarantined bytes could not be purged on the first attempt.
    service.delete_attachment(attachment.id)

    assert service.list_attachments(node.id) == ()

    # The journal entry survives the purge failure, so reconciliation can finish the cleanup.
    (entry,) = pending_operations.list_all()
    assert entry.quarantine_token is not None

    recovering_service = FileService(
        SqliteNodeRepository(sqlite_connection),
        SqliteAttachmentRepository(sqlite_connection),
        SqliteFileReferenceRepository(sqlite_connection),
        real_store,
        pending_operations,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
        current_machine_name=lambda: "laptop",
    )
    recovering_service.reconcile_pending_operations()

    assert pending_operations.list_all() == ()


def _log_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(record).items()
        if key not in logging.LogRecord("x", 0, "x", 0, "x", (), None).__dict__
    }


def test_upload_and_download_log_bounded_fields_without_filenames_or_paths(
    sqlite_connection: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    with caplog.at_level(logging.INFO, logger="personal_graph_os.application.file_service"):
        attachment = service.upload_attachment(
            node_id, file_name="secret-plans.pdf", mime_type="application/pdf", chunks=[b"hello"]
        )
        service.download_attachment(attachment.id)

    upload_record, download_record = caplog.records
    upload_fields = _log_fields(upload_record)
    download_fields = _log_fields(download_record)

    assert upload_fields["operation"] == "upload"
    assert upload_fields["outcome"] == "success"
    assert upload_fields["size_bytes"] == len(b"hello")
    assert download_fields["operation"] == "download"
    assert download_fields["outcome"] == "success"

    for fields in (upload_fields, download_fields):
        assert "secret-plans.pdf" not in str(fields)
        assert str(tmp_path) not in str(fields)


def test_upload_failure_logs_a_bounded_error_category(
    sqlite_connection: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)

    with caplog.at_level(logging.INFO, logger="personal_graph_os.application.file_service"):
        with pytest.raises(UploadTooLargeError):
            service.upload_attachment(
                node_id,
                file_name="big.bin",
                mime_type="application/octet-stream",
                chunks=[b"x" * 2000],
            )

    (record,) = caplog.records
    fields = _log_fields(record)
    assert fields["operation"] == "upload"
    assert fields["outcome"] == "failure"
    assert fields["error_category"] == "UploadTooLargeError"


def test_delete_attachment_logs_success(
    sqlite_connection: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    attachment = service.upload_attachment(
        node_id, file_name="notes.pdf", mime_type="application/pdf", chunks=[b"hello"]
    )

    with caplog.at_level(logging.INFO, logger="personal_graph_os.application.file_service"):
        service.delete_attachment(attachment.id)

    delete_records = [r for r in caplog.records if _log_fields(r).get("operation") == "delete"]
    assert len(delete_records) == 1
    fields = _log_fields(delete_records[0])
    assert fields["outcome"] == "success"
    assert "notes.pdf" not in str(fields)


def test_verify_file_reference_logs_a_failure_when_recorded_for_a_different_machine(
    sqlite_connection: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    reference = service.create_file_reference(
        node_id,
        machine_name="a-different-machine",
        relative_path="source.md",
        absolute_path=str(tmp_path / "source.md"),
    )

    with caplog.at_level(logging.INFO, logger="personal_graph_os.application.file_service"):
        with pytest.raises(UnverifiableFileReferenceError):
            service.verify_file_reference(reference.id)

    verify_records = [
        r for r in caplog.records if _log_fields(r).get("operation") == "verify_reference"
    ]
    assert len(verify_records) == 1
    fields = _log_fields(verify_records[0])
    assert fields["outcome"] == "failure"
    assert fields["error_category"] == "UnverifiableFileReferenceError"


def test_verify_file_reference_logs_success(
    sqlite_connection: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, node_id = _service_with_node(sqlite_connection, tmp_path)
    real_file = tmp_path / "source.md"
    real_file.write_text("hello")
    reference = service.create_file_reference(
        node_id, machine_name="laptop", relative_path="source.md", absolute_path=str(real_file)
    )

    with caplog.at_level(logging.INFO, logger="personal_graph_os.application.file_service"):
        service.verify_file_reference(reference.id)

    verify_records = [
        r for r in caplog.records if _log_fields(r).get("operation") == "verify_reference"
    ]
    assert len(verify_records) == 1
    assert _log_fields(verify_records[0])["outcome"] == "success"
