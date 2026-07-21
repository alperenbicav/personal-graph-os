"""Managed attachments and non-copying file/repository references.

A `FileReference` never implies the source file was copied or ingested; it records where
to find it (machine, repository, path, optional Git ref) and whether that location was
last verified to exist. An `Attachment` is an explicit, copied, checksummed file.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import AttachmentId, FileReferenceId, NodeId, new_id


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


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

    @field_validator("size_bytes")
    @classmethod
    def _validate_size(cls, value: int) -> int:
        if value < 0:
            raise InvariantViolationError("Attachment.size_bytes must not be negative")
        return value


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

    @field_validator("machine_name", "relative_path")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "FileReference field")
        return _non_empty(value, f"FileReference.{field_name}")
