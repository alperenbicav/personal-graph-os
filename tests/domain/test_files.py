from __future__ import annotations

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.identifiers import NodeId, new_id

_VALID_CHECKSUM = "a" * 64


def _attachment(
    *,
    file_name: str = "notes.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 1024,
    checksum_sha256: str = _VALID_CHECKSUM,
    storage_relative_path: str = "ab/cdef0123",
) -> Attachment:
    return Attachment(
        node_id=NodeId(new_id()),
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        storage_relative_path=storage_relative_path,
    )


def _file_reference(
    *,
    machine_name: str = "laptop",
    relative_path: str = "repo/README.md",
    repository_name: str | None = None,
    absolute_path: str | None = None,
) -> FileReference:
    return FileReference(
        node_id=NodeId(new_id()),
        machine_name=machine_name,
        relative_path=relative_path,
        repository_name=repository_name,
        absolute_path=absolute_path,
    )


def test_attachment_accepts_valid_fields() -> None:
    attachment = _attachment()
    assert attachment.file_name == "notes.pdf"
    assert attachment.checksum_sha256 == _VALID_CHECKSUM


def test_attachment_rejects_a_blank_file_name() -> None:
    with pytest.raises(InvariantViolationError, match="must not be empty"):
        _attachment(file_name="   ")


def test_attachment_rejects_a_blank_mime_type() -> None:
    with pytest.raises(InvariantViolationError, match="must not be empty"):
        _attachment(mime_type="   ")


def test_attachment_rejects_a_blank_storage_relative_path() -> None:
    with pytest.raises(InvariantViolationError, match="must not be empty"):
        _attachment(storage_relative_path="   ")


def test_attachment_rejects_negative_size() -> None:
    with pytest.raises(InvariantViolationError, match="must not be negative"):
        _attachment(size_bytes=-1)


@pytest.mark.parametrize("checksum", ["short", "g" * 64, _VALID_CHECKSUM.upper()])
def test_attachment_rejects_malformed_checksum(checksum: str) -> None:
    with pytest.raises(InvariantViolationError, match="64 lowercase hex"):
        _attachment(checksum_sha256=checksum)


@pytest.mark.parametrize("file_name", ["../secret.pdf", "sub/dir.pdf", "back\\slash.pdf"])
def test_attachment_rejects_path_separators_in_file_name(file_name: str) -> None:
    with pytest.raises(InvariantViolationError, match="path separator"):
        _attachment(file_name=file_name)


@pytest.mark.parametrize("storage_relative_path", ["../escape", "/absolute", "a/../b"])
def test_attachment_rejects_unsafe_storage_relative_path(storage_relative_path: str) -> None:
    with pytest.raises(InvariantViolationError, match="relative path"):
        _attachment(storage_relative_path=storage_relative_path)


def test_file_reference_accepts_a_repository_only_pointer() -> None:
    reference = _file_reference(repository_name="apilex-agent", absolute_path=None)
    assert reference.absolute_path is None
    assert reference.is_missing is False


def test_file_reference_accepts_a_verifiable_absolute_path() -> None:
    reference = _file_reference(absolute_path="/Users/alice/repo/README.md")
    assert reference.absolute_path == "/Users/alice/repo/README.md"


def test_file_reference_rejects_a_relative_absolute_path() -> None:
    with pytest.raises(InvariantViolationError, match="absolute path"):
        _file_reference(absolute_path="relative/path.md")


def test_file_reference_rejects_a_blank_machine_name() -> None:
    with pytest.raises(InvariantViolationError, match="must not be empty"):
        _file_reference(machine_name="  ")


def test_file_reference_rejects_a_blank_relative_path() -> None:
    with pytest.raises(InvariantViolationError, match="must not be empty"):
        _file_reference(relative_path="  ")


@pytest.mark.parametrize("relative_path", ["../secret", "/absolute/path", "a/../b"])
def test_file_reference_rejects_an_unsafe_relative_path(relative_path: str) -> None:
    with pytest.raises(InvariantViolationError, match="relative path"):
        _file_reference(relative_path=relative_path)


def test_file_reference_rejects_an_oversized_relative_path() -> None:
    with pytest.raises(InvariantViolationError, match="must not exceed"):
        _file_reference(relative_path="a" * 5000)


def test_file_reference_rejects_an_oversized_absolute_path() -> None:
    with pytest.raises(InvariantViolationError, match="must not exceed"):
        _file_reference(absolute_path="/" + "a" * 5000)


def test_file_reference_rejects_an_oversized_machine_name() -> None:
    with pytest.raises(InvariantViolationError, match="must not exceed"):
        _file_reference(machine_name="a" * 300)


def test_attachment_evidence_pointer_is_the_documented_scheme() -> None:
    attachment = _attachment()
    assert attachment.evidence_pointer == f"attachment:{attachment.id}"


def test_file_reference_evidence_pointer_is_the_documented_scheme() -> None:
    reference = _file_reference()
    assert reference.evidence_pointer == f"file-reference:{reference.id}"
