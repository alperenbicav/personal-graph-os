"""The only path through which managed `Attachment` bytes are written/read and
non-copying `FileReference` verification happens.

Both aggregates carry only a `node_id`, never their own `workspace_id`: workspace scope is
always derived by first resolving the owning `Node`, so a caller can never address another
workspace's data by attachment/reference id alone without also knowing a `Node` in it.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from personal_graph_os.application.file_storage import (
    ManagedFileStore,
    PendingFileOperationRepository,
)
from personal_graph_os.application.repositories import (
    AttachmentRepository,
    FileReferenceRepository,
    NodeRepository,
)
from personal_graph_os.domain.errors import DomainError, UnknownSchemaReferenceError
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import AttachmentId, FileReferenceId, NodeId, new_id

DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_logger = logging.getLogger(__name__)


def _log_file_operation(
    operation: str,
    outcome: str,
    *,
    size_bytes: int | None = None,
    error: BaseException | None = None,
) -> None:
    """Bounded, privacy-safe observability (approved plan): operation/result/byte counts only.

    Never includes a filename, absolute/relative path, file content, bearer token, or raw
    exception message — only the failing exception's class name, which is a bounded category.
    """
    fields: dict[str, object] = {"operation": operation, "outcome": outcome}
    if size_bytes is not None:
        fields["size_bytes"] = size_bytes
    if error is not None:
        fields["error_category"] = type(error).__name__
    _logger.info("file_operation", extra=fields)


class NodeNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a node that does not exist."""


class AttachmentNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references an attachment that does not exist."""


class FileReferenceNotFoundError(UnknownSchemaReferenceError):
    """Raised when an operation references a file reference that does not exist."""


class UnverifiableFileReferenceError(DomainError):
    """Raised when verification is requested for a reference with no `absolute_path`, or one
    recorded for a different machine than the one performing the check (decision #9: only an
    absolute path on the current machine can be checked locally)."""


class FileService:
    def __init__(
        self,
        nodes: NodeRepository,
        attachments: AttachmentRepository,
        file_references: FileReferenceRepository,
        store: ManagedFileStore,
        pending_operations: PendingFileOperationRepository,
        *,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        current_machine_name: Callable[[], str] = socket.gethostname,
    ) -> None:
        self._nodes = nodes
        self._attachments = attachments
        self._file_references = file_references
        self._store = store
        self._pending_operations = pending_operations
        self._max_upload_bytes = max_upload_bytes
        self._current_machine_name = current_machine_name

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    def _require_node(self, node_id: NodeId) -> Node:
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node {node_id} does not exist")
        return node

    def _require_attachment(self, attachment_id: AttachmentId) -> Attachment:
        attachment = self._attachments.get(attachment_id)
        if attachment is None:
            raise AttachmentNotFoundError(f"attachment {attachment_id} does not exist")
        return attachment

    def _require_file_reference(self, file_reference_id: FileReferenceId) -> FileReference:
        reference = self._file_references.get(file_reference_id)
        if reference is None:
            raise FileReferenceNotFoundError(f"file reference {file_reference_id} does not exist")
        return reference

    def list_attachments(self, node_id: NodeId) -> tuple[Attachment, ...]:
        self._require_node(node_id)
        return self._attachments.list_by_node(node_id)

    def upload_attachment(
        self, node_id: NodeId, *, file_name: str, mime_type: str, chunks: Iterable[bytes]
    ) -> Attachment:
        self._require_node(node_id)
        # Validate client metadata against the exact domain invariants before any store I/O:
        # an invalid file_name/mime_type must never finalize bytes it would then have to
        # compensate for (ST05-F01). `id`/size/checksum/path are placeholders re-validated
        # for real once the upload actually completes below.
        try:
            Attachment(
                node_id=node_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=0,
                checksum_sha256="0" * 64,
                storage_relative_path="validation-only",
            )
        except BaseException as error:
            _log_file_operation("upload", "failure", error=error)
            raise

        try:
            uploaded = self._store.receive_upload(chunks, max_size_bytes=self._max_upload_bytes)
        except BaseException as error:
            _log_file_operation("upload", "failure", error=error)
            raise

        attachment_id = AttachmentId(new_id())
        try:
            storage_relative_path = self._store.finalize(uploaded, attachment_id)
        except BaseException as error:
            self._store.discard(uploaded)
            _log_file_operation("upload", "failure", error=error)
            raise

        # Durably record that this file now exists at `storage_relative_path` before the row
        # is committed: if the save below fails *and* its compensation delete also fails, this
        # entry survives so `reconcile_pending_operations()` can still remove the orphan after
        # a restart instead of it being silently lost (ST05-F01).
        journal_entry = self._pending_operations.record(
            attachment_id=attachment_id,
            storage_relative_path=storage_relative_path,
            quarantine_token=None,
        )

        try:
            attachment = Attachment(
                id=attachment_id,
                node_id=node_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=uploaded.size_bytes,
                checksum_sha256=uploaded.checksum_sha256,
                storage_relative_path=storage_relative_path,
            )
            self._attachments.save(attachment)
        except BaseException as error:
            # Compensate: the row was never committed, so the finalized file would
            # otherwise be an orphan with nothing referencing it. If the compensation delete
            # itself fails, the original error is what the caller must see — it is the actual
            # cause — with the compensation failure attached as context, not swallowed. The
            # journal entry is left in place so reconciliation can finish the cleanup later.
            try:
                self._store.delete(storage_relative_path)
            except BaseException as compensation_error:
                _log_file_operation("upload", "failure", error=compensation_error)
                _log_file_operation("upload", "failure", error=error)
                raise error from compensation_error
            self._pending_operations.remove(journal_entry.id)
            _log_file_operation("upload", "failure", error=error)
            raise
        self._pending_operations.remove(journal_entry.id)
        _log_file_operation("upload", "success", size_bytes=attachment.size_bytes)
        return attachment

    def download_attachment(self, attachment_id: AttachmentId) -> tuple[Attachment, BinaryIO]:
        attachment = self._require_attachment(attachment_id)
        try:
            opened = self._store.open_verified(
                attachment.storage_relative_path,
                expected_size_bytes=attachment.size_bytes,
                expected_checksum_sha256=attachment.checksum_sha256,
            )
        except BaseException as error:
            _log_file_operation("download", "failure", error=error)
            raise
        _log_file_operation("download", "success", size_bytes=attachment.size_bytes)
        return attachment, opened

    def delete_attachment(self, attachment_id: AttachmentId) -> None:
        """Delete an attachment's row and bytes so neither ever exists without the other.

        Quarantines the bytes (a reversible move, not a deletion) before touching the row, and
        durably journals that quarantine before attempting the row delete. If the row delete
        fails, the bytes are restored so the attachment is exactly as it was — present and
        downloadable, safe to retry — even if that restore attempt itself fails, because the
        journal entry survives for `reconcile_pending_operations()` to finish later. Only once
        the row delete has actually succeeded is the quarantined copy purged, and a purge
        failure there is likewise left for reconciliation rather than treated as fatal or as a
        silently accepted orphan (ST05-F01).
        """
        attachment = self._require_attachment(attachment_id)
        try:
            quarantine_token = self._store.quarantine(attachment.storage_relative_path)
        except BaseException as error:
            _log_file_operation("delete", "failure", error=error)
            raise

        journal_entry = self._pending_operations.record(
            attachment_id=attachment_id,
            storage_relative_path=attachment.storage_relative_path,
            quarantine_token=quarantine_token,
        )

        try:
            self._attachments.delete(attachment_id)
        except BaseException as error:
            try:
                self._store.restore(quarantine_token, attachment.storage_relative_path)
            except BaseException as compensation_error:
                # The row still exists, so reconciliation will retry the restore later; the
                # original repository error is still what the caller sees.
                _log_file_operation("delete", "failure", error=compensation_error)
                _log_file_operation("delete", "failure", error=error)
                raise error from compensation_error
            self._pending_operations.remove(journal_entry.id)
            _log_file_operation("delete", "failure", error=error)
            raise

        try:
            self._store.purge_quarantined(quarantine_token)
        except BaseException as error:
            # The row is already gone, so the attachment is correctly deleted from the user's
            # perspective; the journal entry is left so reconciliation retries the purge later
            # instead of this being silently accepted as a permanent, untracked orphan.
            _log_file_operation("delete", "failure", error=error)
            return
        self._pending_operations.remove(journal_entry.id)
        _log_file_operation("delete", "success", size_bytes=attachment.size_bytes)

    def reconcile_pending_operations(self) -> None:
        """Finish any managed-file operation left incomplete by a crash or a compensation
        failure (ST05-F01): for each durable journal entry, check whether the attachment row
        still exists and complete whichever side of the operation never finished. Safe to call
        repeatedly (e.g. on every app startup) — a fully reconciled journal is a no-op.
        """
        for entry in self._pending_operations.list_all():
            row_exists = self._attachments.get(entry.attachment_id) is not None
            try:
                if entry.quarantine_token is None:
                    # Upload path: the file was finalized. If its row never committed, the
                    # file is an orphan to remove; if the row exists, the upload actually
                    # succeeded and there is nothing left to do.
                    if not row_exists:
                        self._store.delete(entry.storage_relative_path)
                elif row_exists:
                    # Delete path, row still present: the delete never completed, so restore.
                    self._store.restore(entry.quarantine_token, entry.storage_relative_path)
                else:
                    # Delete path, row gone: the delete completed; finish purging the bytes.
                    self._store.purge_quarantined(entry.quarantine_token)
            except BaseException as error:
                _log_file_operation("reconcile", "failure", error=error)
                continue
            self._pending_operations.remove(entry.id)
            _log_file_operation("reconcile", "success")

    def list_file_references(self, node_id: NodeId) -> tuple[FileReference, ...]:
        self._require_node(node_id)
        return self._file_references.list_by_node(node_id)

    def create_file_reference(
        self,
        node_id: NodeId,
        *,
        machine_name: str,
        relative_path: str,
        repository_name: str | None = None,
        absolute_path: str | None = None,
        git_ref: str | None = None,
    ) -> FileReference:
        self._require_node(node_id)
        reference = FileReference(
            node_id=node_id,
            machine_name=machine_name,
            relative_path=relative_path,
            repository_name=repository_name,
            absolute_path=absolute_path,
            git_ref=git_ref,
        )
        self._file_references.save(reference)
        return reference

    def delete_file_reference(self, file_reference_id: FileReferenceId) -> None:
        self._require_file_reference(file_reference_id)
        self._file_references.delete(file_reference_id)

    def verify_file_reference(self, file_reference_id: FileReferenceId) -> FileReference:
        """Check the reference's `absolute_path` on this machine and record the result.

        Only verifies a reference recorded for the machine actually performing the check —
        otherwise the recorded `last_verified_at` would overclaim that a *different* machine's
        filesystem was inspected (ST05-F04). Never resolves `relative_path`/`repository_name`
        against anything: those are provenance labels, not claims that a remote or
        repository-relative location was inspected (decision #9). A recorded location that is
        itself a symlink is treated as unverifiable-present: this application never follows a
        symlink for managed storage (decision #8's symlink rule) and extends the same
        conservative rule to what it will claim as "verified present" here.
        """
        try:
            reference = self._require_file_reference(file_reference_id)
            if reference.absolute_path is None:
                raise UnverifiableFileReferenceError(
                    f"file reference {file_reference_id} has no absolute_path and cannot be "
                    "verified on this machine"
                )
            current_machine = self._current_machine_name()
            if reference.machine_name != current_machine:
                raise UnverifiableFileReferenceError(
                    f"file reference {file_reference_id} is recorded for machine "
                    f"{reference.machine_name!r}, not the current machine {current_machine!r}"
                )
            path = Path(reference.absolute_path)
            present = path.exists() and not path.is_symlink()
            verified = reference.model_copy(
                update={"is_missing": not present, "last_verified_at": datetime.now(UTC)}
            )
            self._file_references.save(verified)
        except BaseException as error:
            _log_file_operation("verify_reference", "failure", error=error)
            raise
        _log_file_operation("verify_reference", "success")
        return verified
