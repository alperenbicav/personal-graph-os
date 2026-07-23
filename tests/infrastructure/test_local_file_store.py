from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from personal_graph_os.domain.errors import (
    AttachmentContentCorruptedError,
    AttachmentContentMissingError,
    UploadTooLargeError,
)
from personal_graph_os.domain.identifiers import AttachmentId, new_id
from personal_graph_os.infrastructure.local_file_store import LocalManagedFileStore


def _store(tmp_path: Path) -> LocalManagedFileStore:
    return LocalManagedFileStore(tmp_path / "managed-root")


def test_receive_upload_streams_chunks_and_computes_checksum_and_size(tmp_path: Path) -> None:
    store = _store(tmp_path)
    chunks = [b"hello ", b"world"]

    uploaded = store.receive_upload(chunks, max_size_bytes=1024)

    assert uploaded.size_bytes == len(b"hello world")
    assert uploaded.checksum_sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert Path(uploaded.temp_path).exists()


def test_receive_upload_over_the_limit_raises_and_removes_the_partial_file(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(UploadTooLargeError):
        store.receive_upload([b"1234567890"], max_size_bytes=5)

    staged = list((tmp_path / "managed-root" / ".staging").iterdir())
    assert staged == []


def test_finalize_moves_the_staged_file_under_the_attachment_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploaded = store.receive_upload([b"content"], max_size_bytes=1024)
    attachment_id = AttachmentId(new_id())

    storage_relative_path = store.finalize(uploaded, attachment_id)

    assert storage_relative_path == attachment_id
    assert not Path(uploaded.temp_path).exists()
    with store.open_read(storage_relative_path) as opened:
        assert opened.read() == b"content"


def test_discard_removes_a_staged_upload_that_will_never_be_finalized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploaded = store.receive_upload([b"content"], max_size_bytes=1024)

    store.discard(uploaded)

    assert not Path(uploaded.temp_path).exists()
    store.discard(uploaded)  # idempotent


def test_delete_removes_a_finalized_attachment_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploaded = store.receive_upload([b"content"], max_size_bytes=1024)
    storage_relative_path = store.finalize(uploaded, AttachmentId(new_id()))

    store.delete(storage_relative_path)
    store.delete(storage_relative_path)  # idempotent: missing is not an error

    with pytest.raises(AttachmentContentMissingError):
        store.open_read(storage_relative_path)


def test_open_read_refuses_a_path_that_escapes_the_managed_root(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="escapes the managed root"):
        store.open_read("../outside")


def test_quarantine_restore_round_trips_without_deleting_the_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploaded = store.receive_upload([b"content"], max_size_bytes=1024)
    storage_relative_path = store.finalize(uploaded, AttachmentId(new_id()))

    token = store.quarantine(storage_relative_path)
    with pytest.raises(AttachmentContentMissingError):
        store.open_read(storage_relative_path)

    store.restore(token, storage_relative_path)
    with store.open_read(storage_relative_path) as opened:
        assert opened.read() == b"content"


def test_purge_quarantined_removes_the_file_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploaded = store.receive_upload([b"content"], max_size_bytes=1024)
    storage_relative_path = store.finalize(uploaded, AttachmentId(new_id()))
    token = store.quarantine(storage_relative_path)

    store.purge_quarantined(token)
    store.purge_quarantined(token)  # idempotent: missing is not an error

    with pytest.raises(FileNotFoundError):
        store.restore(token, storage_relative_path)


def test_open_verified_never_follows_a_symlink_placed_at_the_managed_leaf(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_upload = store.receive_upload([b"real content"], max_size_bytes=1024)
    real_path = store.finalize(real_upload, AttachmentId(new_id()))

    victim_upload = store.receive_upload([b"victim content"], max_size_bytes=1024)
    victim_path = store.finalize(victim_upload, AttachmentId(new_id()))

    # Simulate the managed root being tampered with: the victim's file is replaced by a
    # symlink pointing at the real attachment's file.
    managed_root = tmp_path / "managed-root"
    (managed_root / victim_path).unlink()
    (managed_root / victim_path).symlink_to(managed_root / real_path)

    with pytest.raises(AttachmentContentCorruptedError):
        store.open_verified(
            victim_path,
            expected_size_bytes=len(b"victim content"),
            expected_checksum_sha256=hashlib.sha256(b"victim content").hexdigest(),
        )


def test_delete_removes_only_the_symlink_never_its_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_upload = store.receive_upload([b"real content"], max_size_bytes=1024)
    real_path = store.finalize(real_upload, AttachmentId(new_id()))

    victim_upload = store.receive_upload([b"victim content"], max_size_bytes=1024)
    victim_path = store.finalize(victim_upload, AttachmentId(new_id()))
    managed_root = tmp_path / "managed-root"
    (managed_root / victim_path).unlink()
    (managed_root / victim_path).symlink_to(managed_root / real_path)

    store.delete(victim_path)

    assert not (managed_root / victim_path).exists()
    with store.open_read(real_path) as opened:
        assert opened.read() == b"real content"
