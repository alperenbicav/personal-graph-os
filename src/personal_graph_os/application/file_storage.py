"""Managed-storage boundary: the only place attachment bytes are written or read.

`ManagedFileStore` receives an upload as a stream into a temporary location so a client
never dictates `Attachment.storage_relative_path` (decision #8), enforces the configured
size limit while streaming rather than after buffering the whole payload, and finalizes to
a path derived from the attachment id. `discard` and `delete` are idempotent compensation
hooks: an upload that fails before or after finalization must never leave a temporary or
managed-root file with no corresponding committed row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol

from personal_graph_os.domain.identifiers import AttachmentId


@dataclass(frozen=True)
class UploadedFile:
    """A completed, hashed, size-checked upload staged outside the managed root."""

    temp_path: str
    size_bytes: int
    checksum_sha256: str


@dataclass(frozen=True)
class PendingFileOperation:
    """A durable journal entry recording a managed-file operation that is not yet known to
    have completed, so it can be finished by `FileService.reconcile_pending_operations()`
    even if the process that started it crashed or a compensation step itself failed
    (ST05-F01). `quarantine_token` is `None` for an upload (the file was finalized and might
    need deleting if its row never committed) and set for a delete (the file was moved to
    quarantine and might need restoring or purging depending on whether the row was removed).
    """

    id: str
    attachment_id: AttachmentId
    storage_relative_path: str
    quarantine_token: str | None
    created_at: datetime


class PendingFileOperationRepository(Protocol):
    def record(
        self,
        *,
        attachment_id: AttachmentId,
        storage_relative_path: str,
        quarantine_token: str | None,
    ) -> PendingFileOperation: ...
    def remove(self, entry_id: str) -> None: ...
    def list_all(self) -> tuple[PendingFileOperation, ...]: ...


class ManagedFileStore(Protocol):
    def receive_upload(self, chunks: Iterable[bytes], *, max_size_bytes: int) -> UploadedFile:
        """Stream `chunks` into a temporary staging file, hashing as it goes.

        Raises `personal_graph_os.domain.errors.UploadTooLargeError` and removes the
        partial temporary file as soon as `max_size_bytes` is exceeded.
        """
        ...

    def finalize(self, uploaded: UploadedFile, attachment_id: AttachmentId) -> str:
        """Move a staged upload into the managed root; return its storage-relative path."""
        ...

    def discard(self, uploaded: UploadedFile) -> None:
        """Remove a staged upload that will never be finalized. Idempotent."""
        ...

    def open_read(self, storage_relative_path: str) -> BinaryIO:
        """Open a finalized attachment's bytes for reading, unverified."""
        ...

    def open_verified(
        self, storage_relative_path: str, *, expected_size_bytes: int, expected_checksum_sha256: str
    ) -> BinaryIO:
        """Open a finalized attachment's bytes only after confirming they still match its
        recorded size and SHA-256 checksum.

        Raises `personal_graph_os.domain.errors.AttachmentContentMissingError` if the file is
        absent, or `AttachmentContentCorruptedError` if it exists but no longer matches —
        never returning a stream for content that could silently diverge from what the
        checksum promises.
        """
        ...

    def delete(self, storage_relative_path: str) -> None:
        """Remove a finalized attachment's bytes outright. Idempotent: missing is not an error.

        Prefer `quarantine`/`restore`/`purge_quarantined` when the deletion must remain
        reversible until a paired repository write also succeeds.
        """
        ...

    def quarantine(self, storage_relative_path: str) -> str:
        """Move a finalized attachment's bytes to a holding area without deleting them.

        Returns an opaque token that `restore` or `purge_quarantined` accepts. Used to make a
        cross-resource delete reversible: quarantine the bytes, delete the repository row, and
        only then `purge_quarantined` — or `restore` if the row delete failed — so the
        attachment is never left with a row and no bytes, or bytes and no row.
        """
        ...

    def restore(self, quarantine_token: str, storage_relative_path: str) -> None:
        """Move a quarantined file back to its original storage-relative path."""
        ...

    def purge_quarantined(self, quarantine_token: str) -> None:
        """Permanently remove a quarantined file. Idempotent: missing is not an error."""
        ...
