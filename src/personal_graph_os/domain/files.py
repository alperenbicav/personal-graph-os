"""Managed attachments and non-copying file/repository references.

A `FileReference` never implies the source file was copied or ingested; it records where
to find it (machine, repository, path, optional Git ref) and whether that location was
last verified to exist. An `Attachment` is an explicit, copied, checksummed file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, computed_field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import AttachmentId, FileReferenceId, NodeId, new_id

_CHECKSUM_SHA256_LENGTH = 64
_CHECKSUM_SHA256_ALPHABET = frozenset("0123456789abcdef")

# Bounds on client-supplied metadata: unbounded strings on an authenticated-but-local API are
# still a local memory/storage DoS vector (decision-adjacent hardening from ST05-F04).
_MAX_NAME_LENGTH = 256
_MAX_PATH_LENGTH = 4096


def _non_empty(value: str, field_label: str, *, max_length: int = _MAX_NAME_LENGTH) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    if "\x00" in stripped:
        raise InvariantViolationError(f"{field_label} must not contain a NUL byte")
    if len(stripped) > max_length:
        raise InvariantViolationError(f"{field_label} must not exceed {max_length} characters")
    return stripped


def _reject_path_separators(value: str, field_label: str) -> str:
    """Keep a display-metadata field (e.g. a filename) from smuggling in a path.

    `Attachment.file_name` is untrusted client metadata shown to the user, never resolved
    against the filesystem; rejecting separators here keeps that boundary explicit even
    before the API layer's own traversal checks on the resolved storage path.
    """
    if "/" in value or "\\" in value:
        raise InvariantViolationError(f"{field_label} must not contain a path separator")
    return value


def _reject_unsafe_relative_path(value: str, field_label: str) -> str:
    """Reject a traversal/absolute shape in a path a caller records as "relative to
    somewhere" — a provenance label, never resolved by this application, but still not
    allowed to read as an attempt to escape its own recorded base."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise InvariantViolationError(f"{field_label} must be a relative path with no '..' segment")
    return value


class Attachment(BaseModel):
    """A managed, copied file with accounted storage and checksum."""

    id: AttachmentId = Field(default_factory=lambda: AttachmentId(new_id()))
    node_id: NodeId
    file_name: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str
    storage_relative_path: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("file_name", "mime_type", "checksum_sha256", "storage_relative_path")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "Attachment field")
        return _non_empty(value, f"Attachment.{field_name}")

    @field_validator("file_name")
    @classmethod
    def _validate_file_name(cls, value: str) -> str:
        return _reject_path_separators(value, "Attachment.file_name")

    @field_validator("size_bytes")
    @classmethod
    def _validate_size(cls, value: int) -> int:
        if value < 0:
            raise InvariantViolationError("Attachment.size_bytes must not be negative")
        return value

    @field_validator("checksum_sha256")
    @classmethod
    def _validate_checksum(cls, value: str) -> str:
        if len(value) != _CHECKSUM_SHA256_LENGTH or any(
            character not in _CHECKSUM_SHA256_ALPHABET for character in value
        ):
            raise InvariantViolationError(
                "Attachment.checksum_sha256 must be 64 lowercase hex characters"
            )
        return value

    @field_validator("storage_relative_path")
    @classmethod
    def _validate_storage_relative_path(cls, value: str) -> str:
        return _reject_unsafe_relative_path(value, "Attachment.storage_relative_path")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_pointer(self) -> str:
        """Stable pointer reused by ST-06 (decision #10): `attachment:<id>`."""
        return f"attachment:{self.id}"


class FileReference(BaseModel):
    """A pointer to a file or repository location that is never copied by referencing it."""

    id: FileReferenceId = Field(default_factory=lambda: FileReferenceId(new_id()))
    node_id: NodeId
    machine_name: str
    relative_path: str
    repository_name: str | None = None
    absolute_path: str | None = None
    git_ref: str | None = None
    last_verified_at: datetime | None = None
    is_missing: bool = False

    @field_validator("machine_name")
    @classmethod
    def _validate_machine_name(cls, value: str) -> str:
        return _non_empty(value, "FileReference.machine_name")

    @field_validator("relative_path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        checked = _non_empty(value, "FileReference.relative_path", max_length=_MAX_PATH_LENGTH)
        return _reject_unsafe_relative_path(checked, "FileReference.relative_path")

    @field_validator("repository_name", "git_ref")
    @classmethod
    def _validate_optional_name(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        field_name = getattr(info, "field_name", "FileReference field")
        return _non_empty(value, f"FileReference.{field_name}")

    @field_validator("absolute_path")
    @classmethod
    def _validate_absolute_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = _non_empty(value, "FileReference.absolute_path", max_length=_MAX_PATH_LENGTH)
        if not PurePosixPath(stripped).is_absolute():
            raise InvariantViolationError(
                "FileReference.absolute_path must be an absolute path when set"
            )
        return stripped

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_pointer(self) -> str:
        """Stable pointer reused by ST-06 (decision #10): `file-reference:<id>`."""
        return f"file-reference:{self.id}"
