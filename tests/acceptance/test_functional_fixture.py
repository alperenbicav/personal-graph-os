from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO

import httpx
import pytest

from tests.acceptance.functional_fixture import (
    _CUSTOM_EDGE_TYPE_NAME,
    _verify_export,
    build_functional_fixture,
)
from tests.acceptance.running_server import RunningServer

_FAKE_TOKEN = "fake-export-verification-token"


def _baseline_export_files() -> dict[str, bytes]:
    return {
        "workspace.json": json.dumps({"workspace": {"id": "ws-1"}}).encode("utf-8"),
        "activity.json": json.dumps({"events": []}).encode("utf-8"),
        "file_references.json": json.dumps({"file_references": []}).encode("utf-8"),
        "attachments.json": json.dumps({"attachments": []}).encode("utf-8"),
    }


def _manifest_bytes_for(files: dict[str, bytes]) -> bytes:
    entries = [
        {"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    manifest = {
        "format_version": "1",
        "schema_version": "1",
        "workspace_id": "ws-1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "entries": entries,
    }
    return json.dumps(manifest).encode("utf-8")


def _build_export_zip(non_manifest_files: dict[str, bytes]) -> bytes:
    """Builds a real zip whose `manifest.json` is self-consistent with whatever
    `non_manifest_files` contains — including a tampered/unexpected entry, if the caller
    puts one there — so tests can prove `_verify_export` catches what a self-consistent
    manifest alone cannot."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in non_manifest_files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", _manifest_bytes_for(non_manifest_files))
    return buffer.getvalue()


def _build_export_zip_raw(members: list[tuple[str, bytes]], manifest_bytes: bytes) -> bytes:
    """Writes exactly the given `(name, data)` pairs (in order, duplicates allowed) plus a
    caller-supplied `manifest.json` — bypassing `_build_export_zip`'s dict-keyed, one-entry-
    per-name convenience so a test can construct a raw duplicate ZIP member."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
        archive.writestr("manifest.json", manifest_bytes)
    return buffer.getvalue()


def test_functional_fixture_covers_every_st09_invariant(running_server: RunningServer) -> None:
    summary = build_functional_fixture(
        running_server.base_url, running_server.mcp_url, running_server.token
    )

    assert summary.workspace_id == running_server.workspace_id
    assert summary.default_node_type_id
    # Two explicit REST nodes + one MCP node + one backing node per resource (the fixture's
    # own explicit resource and discovery's imported candidate both get one — product
    # decision #1: a Resource's Node owns graph/content/archive).
    assert summary.node_count == 5
    assert summary.edge_count == 1
    # The default workspace already has one canvas from app bootstrap, plus the two this
    # fixture creates.
    assert summary.canvas_count == 3
    assert summary.placement_count == 2
    assert summary.resource_count == 2
    assert summary.discovery_run_id
    assert summary.attachment_count == 1
    assert summary.attachment_id
    assert summary.file_reference_count == 1
    assert summary.saved_view_count == 1
    assert summary.undo_verified is True
    assert summary.context_pack_count == 1
    assert summary.context_pack_id
    assert summary.mcp_node_id
    # The MCP node create is a distinct, separately counted mutation from the REST node
    # count above; asserting `replayed` proves the idempotency receipt actually fired.
    assert summary.mcp_mutation_replayed is True
    assert summary.activity_event_count > 0
    # ST-09.4 also needs this fixture to exercise the real portable-export path.
    assert summary.export_verified is True


def test_functional_fixture_fails_closed_when_a_rest_call_is_rejected(
    running_server: RunningServer,
) -> None:
    """ST09-F04 regression: every write in `build_functional_fixture` is now a checked
    call (`raise_for_status`), so an invalid credential — or any other rejected request —
    must raise rather than let the fixture silently report a truthy-but-wrong summary. A
    bad bearer token is rejected on the very first call (`GET /workspace`), so this fails
    fast without seeding any real data."""
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        build_functional_fixture(
            running_server.base_url, running_server.mcp_url, "not-the-real-token"
        )

    assert excinfo.value.response.status_code == 401


def test_functional_fixture_fails_closed_on_a_rejected_intermediate_write(
    running_server: RunningServer,
) -> None:
    """ST09-F04 regression: the bad-token test above only proves the very first call
    (`GET /workspace`) fails closed. This pre-creates an edge type with the exact name
    `_build_custom_schema` creates several successful writes later, so that specific
    intermediate `POST /edge-types` call is rejected (a domain-invariant name conflict, not
    an auth failure) after multiple earlier writes already succeeded — proving the checked
    helpers stop the whole build rather than continuing past a 4xx anywhere in the sequence."""
    with httpx.Client(
        base_url=running_server.base_url,
        headers={"Authorization": f"Bearer {running_server.token}"},
    ) as client:
        conflicting = client.post(
            "/edge-types",
            json={
                "workspace_id": running_server.workspace_id,
                "name": _CUSTOM_EDGE_TYPE_NAME,
                "inverse_name": "pre-existing conflict",
            },
        )
        conflicting.raise_for_status()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        build_functional_fixture(
            running_server.base_url, running_server.mcp_url, running_server.token
        )

    assert excinfo.value.response.status_code == 422


def test_verify_export_accepts_a_well_formed_baseline_archive() -> None:
    """Sanity baseline the two tampered-archive regressions below diff against."""
    zip_bytes = _build_export_zip(_baseline_export_files())
    assert _verify_export(zip_bytes, _FAKE_TOKEN) is True


def test_verify_export_rejects_a_self_consistent_manifest_claiming_an_unexpected_entry() -> None:
    """ST09-F04 round-4 regression: a prior version of `_verify_export` derived its entry
    allowlist from the manifest's own `entries` list, so a tampered-but-internally-consistent
    manifest that also manifests an extra file (correct hash/size for that extra file too)
    passed verification — exactly the "unexpected-private-metadata.json" probe the round-3
    review reproduced. The fixed top-level allowlist must reject this regardless of what the
    manifest itself claims."""
    files = _baseline_export_files()
    files["unexpected-private-metadata.json"] = json.dumps({"secret": "leak"}).encode("utf-8")

    zip_bytes = _build_export_zip(files)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False


def test_verify_export_rejects_a_forbidden_field_leaked_outside_file_references_json() -> None:
    """ST09-F04 round-4 regression: a prior version only scanned `absolute_path` inside
    `file_references.json`'s own list. This leaks the field from a different, otherwise
    well-formed export file (`workspace.json`), proving the scan must cover every JSON
    member, not just the one field/file the original check happened to know about."""
    files = _baseline_export_files()
    files["workspace.json"] = json.dumps(
        {"workspace": {"id": "ws-1"}, "leaked": {"absolute_path": "/etc/passwd"}}
    ).encode("utf-8")

    zip_bytes = _build_export_zip(files)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False


def test_verify_export_rejects_the_token_appearing_anywhere_including_the_manifest() -> None:
    """The token scan must cover every archive member, including `manifest.json` itself, not
    only the manifested entries."""
    files = _baseline_export_files()
    files["activity.json"] = json.dumps({"events": [], "note": _FAKE_TOKEN}).encode("utf-8")

    zip_bytes = _build_export_zip(files)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False


def test_verify_export_rejects_a_traversal_like_attachment_path() -> None:
    """ST09-F04 round-5 regression: a prior version's attachment-path check was a single
    permissive regex (`[^/]+` per segment) that let a literal `..` id segment through — the
    exact `attachments/../private-bytes` probe the round-4 review reproduced. Parsing exact
    safe segments must reject it even though the manifest self-consistently describes it."""
    files = _baseline_export_files()
    files["attachments/../private-bytes"] = b"leaked bytes"

    zip_bytes = _build_export_zip(files)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False


def test_verify_export_rejects_a_duplicate_raw_zip_member_name() -> None:
    """ST09-F04 round-5 regression: a prior version converted `archive.namelist()` straight
    to a `set`, silently absorbing a duplicate raw ZIP member (two entries sharing the name
    `workspace.json` with different bytes) before any allowlist/hash check ever saw it."""
    baseline = _baseline_export_files()
    duplicated_member = ("workspace.json", b'{"workspace": {"id": "tampered-duplicate"}}')
    members = [(name, data) for name, data in baseline.items()] + [duplicated_member]
    manifest_bytes = _manifest_bytes_for(baseline)

    zip_bytes = _build_export_zip_raw(members, manifest_bytes)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False


def test_verify_export_rejects_a_duplicate_manifest_entry_path() -> None:
    """ST09-F04 round-5 regression: a prior version converted the manifest's entry paths
    straight to a `set`, silently absorbing a duplicate manifest record (two `entries` both
    claiming `workspace.json`, one with a tampered hash) before the hash/size check ran."""
    files = _baseline_export_files()
    workspace_data = files["workspace.json"]
    entries = [
        {
            "path": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for name, data in sorted(files.items())
    ]
    entries.append(
        {
            "path": "workspace.json",
            "size_bytes": len(workspace_data),
            "sha256": "0" * 64,
        }
    )
    manifest_bytes = json.dumps(
        {
            "format_version": "1",
            "schema_version": "1",
            "workspace_id": "ws-1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "entries": entries,
        }
    ).encode("utf-8")

    zip_bytes = _build_export_zip_raw(list(files.items()), manifest_bytes)

    assert _verify_export(zip_bytes, _FAKE_TOKEN) is False
