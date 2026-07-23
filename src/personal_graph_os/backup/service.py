"""Standard-library backup create/verify/restore for a whole workspace directory.

A backup covers exactly two things: the canonical SQLite database (schema, all rows) and
every managed attachment's verified bytes. It excludes the rebuildable full-text search index
and the pending-file-operation journal -- both internal/rebuildable -- and it always excludes
the bearer token file, so a backup is never itself a credential. Unlike portable export, it
*keeps* MCP idempotency receipts (ST07-F02): a receipt and the `ActivityEvent` it guards share
one unique request key, and dropping only the receipt while keeping the event would silently
break ST-06's exact-replay/conflict guarantee for any request made before the backup.

`create_backup` refuses to run while any managed-file operation is still pending
reconciliation (ST05-F01's journal): a pending entry means an upload or delete is mid-flight
and the managed-files directory cannot be assumed consistent with the database yet. It reads
the source database through `sqlite3.Connection.backup()`, the standard-library online-backup
API, which takes a lock only long enough to safely copy pages across concurrently to a
writer -- this is the "shared SQLite write barrier" the plan calls for, provided by SQLite
itself rather than a custom scheme, and it works safely even from a separate process (WAL
mode allows one writer alongside readers, and `.backup()` is exactly such a reader).

Attachments are enumerated *from that same snapshot*, never from the live database (ST07-F01):
`FileService.upload_attachment` always finalizes an attachment's bytes on disk before
committing its row, so any attachment the snapshot's database contains already had its bytes
written before the snapshot was taken. Enumerating from a separate, still-live connection
instead could observe a newly committed row the snapshot itself does not contain (or vice
versa), publishing a self-consistent-looking archive whose database and attachment list
actually disagree. A concurrent delete completing between the snapshot and the later byte-read
still fails the whole backup closed (`AttachmentContentMissingError`), rather than silently
publishing a broken one.

`restore_backup` never touches its destination until every check has passed: it verifies the
archive, extracts into a sibling staging directory, replays migrations and a foreign-key
check on the staged database, and cross-verifies every staged attachment's bytes against the
staged database's own records before the one atomic `os.replace` that installs it. Any
failure before that point leaves the destination exactly as it was -- untouched if it did not
exist, still empty if it did.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from personal_graph_os.domain.errors import (
    AttachmentContentCorruptedError,
    AttachmentContentMissingError,
)
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore
from personal_graph_os.infrastructure.sqlite.connection import open_connection
from personal_graph_os.infrastructure.sqlite.migrations.runner import run_migrations
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteAttachmentRepository,
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)

BACKUP_FORMAT_VERSION = "1"
BACKUP_SCHEMA_VERSION = "1"

# Matches the fixed workspace-directory layout `personal_graph_os.api.app.create_app` uses
# by convention (`DEFAULT_DATABASE_PATH`, `MANAGED_FILES_DIR_NAME`, `auth.TOKEN_FILE_NAME`).
GRAPH_DB_FILE_NAME = "graph.db"
MANAGED_FILES_DIR_NAME = "managed-files"
TOKEN_FILE_NAME = "api-token"

_DATABASE_ENTRY_NAME = "database.sqlite3"
_MANIFEST_ENTRY_NAME = "manifest.json"
_MAX_ATTACHMENT_NAME_LENGTH = 200
_READ_CHUNK_BYTES = 65536

# Format-level caps enforced before any extraction/hashing work (ST07-F07): a corrupt or
# malicious archive can declare an arbitrarily large manifest, entry count, or aggregate
# uncompressed size, so every one of those is checked up front rather than discovered by
# exhausting memory/disk mid-verify.
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 100_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024 * 1024

# Rebuildable/internal tables excluded from the snapshot -- see module docstring. Idempotency
# receipts are deliberately NOT here (ST07-F02): they must survive alongside the activity
# events that reference them.
_EXCLUDED_TABLES = ("search_documents", "pending_file_operations")


class BackupError(Exception):
    """Base type for every error this module raises."""


class PendingFileOperationsError(BackupError):
    """Raised when `create_backup` finds an unreconciled managed-file operation journal
    entry: the managed-files directory cannot be assumed consistent with the database."""


class BackupIntegrityError(BackupError):
    """Raised when a backup archive's contents do not match its own manifest, or contain a
    traversal/symlink/duplicate entry -- the archive is treated as untrustworthy."""


class BackupVersionError(BackupError):
    """Raised when a backup archive declares a format or schema version this build does not
    support."""


class RestoreDestinationNotEmptyError(BackupError):
    """Raised when `restore_backup`'s destination directory exists and is not empty."""


class RestoreValidationError(BackupError):
    """Raised when a staged restore fails migration, foreign-key, or attachment hydration
    validation. The destination is left untouched."""


@dataclass(frozen=True)
class _ManifestEntry:
    path: str
    size_bytes: int
    sha256: str


def _sanitize_entry_name(file_name: str) -> str:
    """Reduce a user-supplied attachment file name to a single safe zip path segment."""
    base = PurePosixPath(file_name.replace("\\", "/")).name
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in base)
    cleaned = cleaned.strip(".") or "file"
    return cleaned[:_MAX_ATTACHMENT_NAME_LENGTH]


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_READ_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _snapshot_database(source_connection: sqlite3.Connection, target_path: Path) -> None:
    """Copy `source_connection`'s database into a fresh file via the standard-library online
    backup API, then strip the excluded rebuildable/internal tables from that copy only --
    the live source database is never mutated."""
    target_connection = sqlite3.connect(target_path)
    try:
        source_connection.backup(target_connection)
        for table_name in _EXCLUDED_TABLES:
            target_connection.execute(f"DELETE FROM {table_name}")
        target_connection.commit()
        target_connection.execute("VACUUM")
    finally:
        target_connection.close()


def create_backup(workspace_dir: Path, output_dir: Path) -> Path:
    """Create a `.pgos-backup.zip` covering `workspace_dir`'s database and managed files.

    Writes into `output_dir` via temp-then-rename so a reader never observes a partial file.
    Raises `PendingFileOperationsError` without writing anything if any managed-file
    operation is still awaiting reconciliation.
    """
    database_path = workspace_dir / GRAPH_DB_FILE_NAME
    if not database_path.exists():
        raise BackupError(f"no database found at {database_path}")

    source_connection = open_connection(database_path)
    try:
        pending_count = source_connection.execute(
            "SELECT COUNT(*) FROM pending_file_operations"
        ).fetchone()[0]
        if pending_count:
            raise PendingFileOperationsError(
                f"{pending_count} pending file operation(s) are not yet reconciled; restart "
                "the app so it can finish reconciling them, then retry the backup"
            )

        managed_store = LocalManagedFileStore(workspace_dir / MANAGED_FILES_DIR_NAME)

        output_dir.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now(UTC)
        snapshot_path = output_dir / f".pgos-backup-snapshot-{uuid.uuid4().hex}.sqlite3"
        descriptor, temp_path_str = tempfile.mkstemp(dir=output_dir, suffix=".pgos-backup.tmp")
        os.close(descriptor)
        temp_path = Path(temp_path_str)
        try:
            _snapshot_database(source_connection, snapshot_path)

            # Enumerate attachments from the snapshot itself, never from the still-live
            # connection (ST07-F01): only this guarantees the attachment list and the
            # database entry describe the exact same point in time. See the module
            # docstring for why reading bytes afterward from live disk is still safe.
            snapshot_read_connection = open_connection(snapshot_path)
            try:
                workspace_repository = SqliteWorkspaceRepository(snapshot_read_connection)
                node_repository = SqliteNodeRepository(snapshot_read_connection)
                attachment_repository = SqliteAttachmentRepository(snapshot_read_connection)
                attachments = []
                for workspace in sorted(workspace_repository.list_all(), key=lambda w: w.id):
                    for node in sorted(
                        node_repository.list_by_workspace(workspace.id, include_archived=True),
                        key=lambda n: n.id,
                    ):
                        attachments.extend(attachment_repository.list_by_node(node.id))
                attachments.sort(key=lambda a: a.id)
            finally:
                snapshot_read_connection.close()

            manifest_entries: list[_ManifestEntry] = []
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                size, digest = _hash_file(snapshot_path)
                archive.write(snapshot_path, arcname=_DATABASE_ENTRY_NAME)
                manifest_entries.append(_ManifestEntry(_DATABASE_ENTRY_NAME, size, digest))

                for attachment in attachments:
                    # `open_verified` raises `AttachmentContentMissingError`/
                    # `AttachmentContentCorruptedError` if the managed bytes no longer match
                    # the recorded size/checksum -- this aborts the whole backup rather than
                    # including stale or partial content (same fail-closed rule as export).
                    opened = managed_store.open_verified(
                        attachment.storage_relative_path,
                        expected_size_bytes=attachment.size_bytes,
                        expected_checksum_sha256=attachment.checksum_sha256,
                    )
                    with opened:
                        data = opened.read()
                    entry_path = (
                        f"attachments/{attachment.id}/{_sanitize_entry_name(attachment.file_name)}"
                    )
                    archive.writestr(entry_path, data)
                    manifest_entries.append(
                        _ManifestEntry(entry_path, len(data), hashlib.sha256(data).hexdigest())
                    )

                manifest = {
                    "format_version": BACKUP_FORMAT_VERSION,
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "generated_at": generated_at.isoformat(),
                    "entries": [
                        {"path": entry.path, "size_bytes": entry.size_bytes, "sha256": entry.sha256}
                        for entry in sorted(manifest_entries, key=lambda entry: entry.path)
                    ],
                }
                archive.writestr(
                    _MANIFEST_ENTRY_NAME,
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
                )
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot_path.unlink(missing_ok=True)

        final_path = (
            output_dir / f"pgos-backup-{generated_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.zip"
        )
        os.replace(temp_path, final_path)
        return final_path
    finally:
        source_connection.close()


def _reject_unsafe_entry_name(name: str) -> None:
    pure = PurePosixPath(name)
    if name.startswith("/") or "\\" in name or pure.is_absolute() or ".." in pure.parts:
        raise BackupIntegrityError(f"unsafe entry name in backup archive: {name!r}")


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(unix_mode) if unix_mode else False


def _validate_manifest_shape(manifest: object) -> dict[str, object]:
    """Validate every manifest field/type before any of it is trusted (ST07-F07): a malformed
    or adversarial manifest must fail closed as `BackupIntegrityError`/`BackupVersionError`,
    never a raw `KeyError`/`TypeError` from later code that assumes a well-formed shape."""
    if not isinstance(manifest, dict):
        raise BackupIntegrityError("manifest.json is not a JSON object")

    format_version = manifest.get("format_version")
    schema_version = manifest.get("schema_version")
    if not isinstance(format_version, str) or not isinstance(schema_version, str):
        raise BackupIntegrityError("manifest.json format_version/schema_version must be strings")
    if format_version != BACKUP_FORMAT_VERSION:
        raise BackupVersionError(f"unsupported backup format version {format_version!r}")
    if schema_version != BACKUP_SCHEMA_VERSION:
        raise BackupVersionError(f"unsupported backup schema version {schema_version!r}")

    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise BackupIntegrityError("backup manifest declares no entries")
    if len(entries) > MAX_MANIFEST_ENTRIES:
        raise BackupIntegrityError(
            f"backup manifest declares more than {MAX_MANIFEST_ENTRIES} entries"
        )

    total_declared_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupIntegrityError("backup manifest entry is not a JSON object")
        path, size_bytes, sha256_hex = (
            entry.get("path"),
            entry.get("size_bytes"),
            entry.get("sha256"),
        )
        if not isinstance(path, str) or not path:
            raise BackupIntegrityError("backup manifest entry has an invalid path")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise BackupIntegrityError(f"{path!r} has an invalid manifest size_bytes")
        if not isinstance(sha256_hex, str) or len(sha256_hex) != 64:
            raise BackupIntegrityError(f"{path!r} has an invalid manifest sha256")
        total_declared_bytes += size_bytes
        if total_declared_bytes > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise BackupIntegrityError(
                f"backup manifest declares more than {MAX_TOTAL_UNCOMPRESSED_BYTES} total bytes"
            )

    return manifest


def verify_backup(backup_path: Path) -> dict[str, object]:
    """Validate a backup archive against its own manifest; return the parsed manifest.

    Rejects a duplicate, traversal, symlink, or unsafely named entry; an oversized or
    malformed manifest (missing/wrong-typed fields, too many entries, too many aggregate
    declared bytes); an unknown/newer format or schema version; any mismatch between the
    manifest's declared entries and the archive's actual entries; and any entry whose actual
    size or SHA-256 does not match what the manifest declares (including an entry that
    expands past its declared size). A corrupt/non-ZIP archive or unparseable manifest JSON
    is translated into `BackupIntegrityError` rather than a raw `BadZipFile`/`JSONDecodeError`.
    """
    try:
        with zipfile.ZipFile(backup_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise BackupIntegrityError("backup archive contains duplicate entry names")
            if _MANIFEST_ENTRY_NAME not in names:
                raise BackupIntegrityError("backup archive is missing manifest.json")

            for info in archive.infolist():
                _reject_unsafe_entry_name(info.filename)
                if _is_symlink_entry(info):
                    raise BackupIntegrityError(f"backup entry {info.filename!r} is a symlink")

            manifest_info = archive.getinfo(_MANIFEST_ENTRY_NAME)
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise BackupIntegrityError(
                    f"manifest.json exceeds the {MAX_MANIFEST_BYTES}-byte limit"
                )
            try:
                raw_manifest = json.loads(archive.read(_MANIFEST_ENTRY_NAME))
            except json.JSONDecodeError as error:
                raise BackupIntegrityError(f"manifest.json is not valid JSON: {error}") from error
            manifest = _validate_manifest_shape(raw_manifest)
            entries = manifest["entries"]
            assert isinstance(entries, list)

            declared_paths = {entry["path"] for entry in entries}
            if len(declared_paths) != len(entries):
                raise BackupIntegrityError("backup manifest declares duplicate paths")
            actual_paths = set(names) - {_MANIFEST_ENTRY_NAME}
            if declared_paths != actual_paths:
                raise BackupIntegrityError(
                    "backup archive entries do not match the manifest (count/expansion mismatch)"
                )

            for entry in entries:
                path, declared_size, declared_hash = (
                    entry["path"],
                    entry["size_bytes"],
                    entry["sha256"],
                )
                info = archive.getinfo(path)
                if info.file_size != declared_size:
                    raise BackupIntegrityError(
                        f"{path} declared size does not match the manifest (expansion mismatch)"
                    )
                digest = hashlib.sha256()
                read_bytes = 0
                with archive.open(path) as stream:
                    while chunk := stream.read(_READ_CHUNK_BYTES):
                        read_bytes += len(chunk)
                        if read_bytes > declared_size:
                            raise BackupIntegrityError(f"{path} expanded past its declared size")
                        digest.update(chunk)
                if read_bytes != declared_size or digest.hexdigest() != declared_hash:
                    raise BackupIntegrityError(
                        f"{path} content does not match the manifest checksum"
                    )
    except zipfile.BadZipFile as error:
        raise BackupIntegrityError(f"not a valid backup archive: {error}") from error

    return manifest


def _validate_staged_database(database_path: Path, managed_dir: Path) -> None:
    """Replay migrations, check foreign-key integrity, and cross-verify every staged
    attachment's bytes against the staged database's own recorded size/checksum -- defense in
    depth against a manifest/database divergence the archive-level check alone cannot see."""
    connection = open_connection(database_path)
    try:
        run_migrations(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RestoreValidationError(
                f"restored database failed foreign-key validation ({len(violations)} violation(s))"
            )

        workspace_repository = SqliteWorkspaceRepository(connection)
        node_repository = SqliteNodeRepository(connection)
        attachment_repository = SqliteAttachmentRepository(connection)
        store = LocalManagedFileStore(managed_dir)

        for workspace in workspace_repository.list_all():
            for node in node_repository.list_by_workspace(workspace.id, include_archived=True):
                for attachment in attachment_repository.list_by_node(node.id):
                    try:
                        opened = store.open_verified(
                            attachment.storage_relative_path,
                            expected_size_bytes=attachment.size_bytes,
                            expected_checksum_sha256=attachment.checksum_sha256,
                        )
                        opened.close()
                    except (
                        AttachmentContentMissingError,
                        AttachmentContentCorruptedError,
                    ) as error:
                        # The archive verified internally, but its database and attachment
                        # entries still disagree with each other (ST07-F01 defense in depth) --
                        # a typed restore failure, never an uncaught domain error.
                        raise RestoreValidationError(
                            f"attachment {attachment.id} for node {node.id} does not match the "
                            f"restored database: {error}"
                        ) from error
    finally:
        connection.close()


def restore_backup(backup_path: Path, destination_dir: Path) -> None:
    """Restore `backup_path` into `destination_dir`, which must not exist or must be empty.

    Extracts and validates entirely inside a sibling staging directory; only the final
    `os.replace` touches `destination_dir`, so any earlier failure leaves it exactly as it
    was. The bearer token is never restored -- a fresh one is created on first boot, matching
    the app's normal first-run behavior.
    """
    if destination_dir.exists():
        if not destination_dir.is_dir():
            raise RestoreDestinationNotEmptyError(
                f"{destination_dir} exists and is not a directory"
            )
        if any(destination_dir.iterdir()):
            raise RestoreDestinationNotEmptyError(f"{destination_dir} is not empty")

    manifest = verify_backup(backup_path)
    manifest_entries = manifest["entries"]
    assert isinstance(manifest_entries, list)

    staging_token = uuid.uuid4().hex[:8]
    staging_dir = (
        destination_dir.parent / f".{destination_dir.name}.pgos-restore-staging-{staging_token}"
    )
    staging_dir.mkdir(parents=True)
    try:
        managed_dir = staging_dir / MANAGED_FILES_DIR_NAME
        managed_dir.mkdir()
        database_path = staging_dir / GRAPH_DB_FILE_NAME

        with zipfile.ZipFile(backup_path) as archive:
            for entry in manifest_entries:
                path = entry["path"]
                if path == _DATABASE_ENTRY_NAME:
                    target = database_path
                elif path.startswith("attachments/"):
                    attachment_id = PurePosixPath(path).parts[1]
                    target = managed_dir / attachment_id
                else:
                    raise RestoreValidationError(f"unexpected backup entry: {path}")
                with archive.open(path) as stream, target.open("wb") as destination_file:
                    shutil.copyfileobj(stream, destination_file)

        _validate_staged_database(database_path, managed_dir)

        if destination_dir.exists():
            destination_dir.rmdir()
        else:
            destination_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_dir, destination_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
