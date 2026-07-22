"""Local filesystem `ManagedFileStore`: bytes live under a server-owned root beside the
SQLite database, never at a client-chosen path.

Staging lives in `<root>/.staging/`; a finalized attachment lives at `<root>/<attachment_id>`,
with no client-supplied extension or path segment, so a filename collision or traversal
attempt in `Attachment.file_name` (display metadata only) can never resolve to a real path.

Path handling never calls `Path.resolve()` on a leaf that a finalized attachment can name:
`resolve()` follows a symlink to its target, so if a managed file were ever replaced by a
symlink (e.g. onto another attachment's file), resolving it would silently redirect every
open/verify/delete onto that target instead of failing closed. Containment under `self._root`
is instead checked lexically against the already domain-validated relative path, and reads use
`os.O_NOFOLLOW` so a symlinked leaf fails outright rather than being dereferenced.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from personal_graph_os.application.file_storage import ManagedFileStore, UploadedFile
from personal_graph_os.domain.errors import (
    AttachmentContentCorruptedError,
    AttachmentContentMissingError,
    UploadTooLargeError,
)
from personal_graph_os.domain.identifiers import AttachmentId

_STAGING_DIRECTORY_NAME = ".staging"
_TRASH_DIRECTORY_NAME = ".trash"
_HASH_READ_CHUNK_BYTES = 65536


class LocalManagedFileStore(ManagedFileStore):
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._staging_dir = self._root / _STAGING_DIRECTORY_NAME
        self._trash_dir = self._root / _TRASH_DIRECTORY_NAME
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._trash_dir.mkdir(parents=True, exist_ok=True)

    def receive_upload(self, chunks: Iterable[bytes], *, max_size_bytes: int) -> UploadedFile:
        temp_path = self._staging_dir / uuid.uuid4().hex
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with temp_path.open("wb") as staged_file:
                for chunk in chunks:
                    size_bytes += len(chunk)
                    if size_bytes > max_size_bytes:
                        raise UploadTooLargeError(f"upload exceeds the {max_size_bytes}-byte limit")
                    digest.update(chunk)
                    staged_file.write(chunk)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return UploadedFile(
            temp_path=str(temp_path),
            size_bytes=size_bytes,
            checksum_sha256=digest.hexdigest(),
        )

    def finalize(self, uploaded: UploadedFile, attachment_id: AttachmentId) -> str:
        destination = self._root / str(attachment_id)
        os.replace(uploaded.temp_path, destination)
        return destination.relative_to(self._root).as_posix()

    def discard(self, uploaded: UploadedFile) -> None:
        Path(uploaded.temp_path).unlink(missing_ok=True)

    def open_read(self, storage_relative_path: str) -> BinaryIO:
        return os.fdopen(self._open_nofollow(storage_relative_path), "rb")

    def open_verified(
        self, storage_relative_path: str, *, expected_size_bytes: int, expected_checksum_sha256: str
    ) -> BinaryIO:
        file_descriptor = self._open_nofollow(storage_relative_path)
        try:
            stat_result = os.fstat(file_descriptor)
            if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size != expected_size_bytes:
                raise AttachmentContentCorruptedError(
                    f"managed file for {storage_relative_path} no longer matches its recorded size"
                )
            digest = hashlib.sha256()
            while chunk := os.read(file_descriptor, _HASH_READ_CHUNK_BYTES):
                digest.update(chunk)
            if digest.hexdigest() != expected_checksum_sha256:
                raise AttachmentContentCorruptedError(
                    f"managed file for {storage_relative_path} no longer matches its recorded "
                    "checksum"
                )
            # Verification and the returned stream share this one file descriptor (opened
            # once, never reopened by path) so nothing can swap the on-disk entry between the
            # check and the read it authorizes.
            os.lseek(file_descriptor, 0, os.SEEK_SET)
        except BaseException:
            os.close(file_descriptor)
            raise
        return os.fdopen(file_descriptor, "rb")

    def delete(self, storage_relative_path: str) -> None:
        """Remove the managed directory entry itself. Idempotent: missing is not an error.

        `Path.unlink()` never dereferences a symlink to remove its target — it always removes
        the named entry — so this stays safe even if a leaf were ever replaced by a symlink.
        """
        self._leaf_path(storage_relative_path).unlink(missing_ok=True)

    def quarantine(self, storage_relative_path: str) -> str:
        source = self._leaf_path(storage_relative_path)
        token = uuid.uuid4().hex
        os.replace(source, self._trash_dir / token)
        return token

    def restore(self, quarantine_token: str, storage_relative_path: str) -> None:
        os.replace(self._trash_dir / quarantine_token, self._leaf_path(storage_relative_path))

    def purge_quarantined(self, quarantine_token: str) -> None:
        (self._trash_dir / quarantine_token).unlink(missing_ok=True)

    def _leaf_path(self, storage_relative_path: str) -> Path:
        """Return the on-disk path for a server-generated relative path, verified lexically.

        `Attachment.storage_relative_path` is domain-validated to be relative with no `..`
        segment before it ever reaches here, so a plain join cannot escape `self._root`; this
        check is defense in depth, not the primary boundary. Deliberately no `Path.resolve()`:
        see the module docstring for why that would be unsafe here.
        """
        relative = PurePosixPath(storage_relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                f"storage_relative_path escapes the managed root: {storage_relative_path}"
            )
        return self._root / storage_relative_path

    def _open_nofollow(self, storage_relative_path: str) -> int:
        path = self._leaf_path(storage_relative_path)
        try:
            return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as error:
            raise AttachmentContentMissingError(
                f"managed file for {storage_relative_path} does not exist"
            ) from error
        except OSError as error:
            # ELOOP (POSIX) when the leaf is a symlink: a managed file must never be served or
            # hashed through a symlink, so treat this the same as content corruption rather
            # than following it.
            raise AttachmentContentCorruptedError(
                f"managed file for {storage_relative_path} is not a regular file"
            ) from error
