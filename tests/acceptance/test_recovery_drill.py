"""ST-09.4: the recovery drill and its corrupted-backup-fails-closed counterpart.

Seeds a real workspace through `build_functional_fixture` (every ST-09 invariant plus a
real portable export), stops that server, backs it up, restores into a fresh empty
directory, starts a second real server against the restored database with a freshly
generated token, then compares canonical REST state, managed-file bytes, rebuilt search,
MCP idempotency replay, and Context Pack materialization between the two servers. Because
this is a byte-for-byte backup/restore of the same database (not a re-seed), every
canonical id is identical before and after — the comparison is a direct equality check, not
a fuzzy one.

Opt-in (`slow`), like every other representative/timed acceptance test in this package:
spawning two real servers and a real backup round trip is not fast enough for the default
`uv run pytest -q` run.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from personal_graph_os.backup.service import (
    BackupIntegrityError,
    create_backup,
    restore_backup,
    verify_backup,
)
from tests.acceptance.functional_fixture import (
    MCP_CREATE_NODE_REQUEST_ID,
    build_functional_fixture,
)
from tests.acceptance.running_server import RunningServer, free_loopback_port


def _rest_client(server: RunningServer) -> httpx.Client:
    return httpx.Client(
        base_url=server.base_url, headers={"Authorization": f"Bearer {server.token}"}
    )


def _canonical_snapshot(server: RunningServer, workspace_id: str) -> dict[str, Any]:
    """Every canonical surface a restore must reproduce byte-for-byte: schema, graph,
    canvases/placements, resources, saved views, and the full activity/audit trail."""
    with _rest_client(server) as client:
        workspace = client.get("/workspace").json()
        nodes = client.get("/nodes", params={"workspace_id": workspace_id}).json()
        edges = client.get("/edges", params={"workspace_id": workspace_id}).json()
        canvases = client.get("/canvases", params={"workspace_id": workspace_id}).json()
        placements = {
            canvas["id"]: client.get(f"/canvases/{canvas['id']}/placements").json()
            for canvas in canvases
        }
        resources = client.get("/resources", params={"workspace_id": workspace_id}).json()
        saved_views = client.get("/saved-views", params={"workspace_id": workspace_id}).json()

        events: list[Any] = []
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"workspace_id": workspace_id, "limit": 200}
            if cursor is not None:
                params["cursor"] = cursor
            page = client.get("/activity-events", params=params).json()
            events.extend(page["events"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    return {
        "node_types": workspace["node_types"],
        "edge_types": workspace["edge_types"],
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": sorted(edges, key=lambda e: e["id"]),
        "canvases": sorted(canvases, key=lambda c: c["id"]),
        "placements": {
            canvas_id: sorted(items, key=lambda p: p["id"])
            for canvas_id, items in placements.items()
        },
        "resources": sorted(resources, key=lambda r: r["id"]),
        "saved_views": sorted(saved_views, key=lambda v: v["id"]),
        "events": sorted(events, key=lambda e: e["id"]),
    }


def _attachment_hash(server: RunningServer, attachment_id: str) -> str:
    with _rest_client(server) as client:
        response = client.get(f"/attachments/{attachment_id}/download")
        response.raise_for_status()
        return hashlib.sha256(response.content).hexdigest()


@asynccontextmanager
async def _mcp_session(server: RunningServer):
    async with streamablehttp_client(
        server.mcp_url, headers={"Authorization": f"Bearer {server.token}"}
    ) as (read, write, _unused_get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _replay_mcp_mutation(
    server: RunningServer, *, workspace_id: str, node_type_id: str
) -> bool:
    """Repeats the exact same idempotent MCP mutation `build_functional_fixture` already made
    before the backup. A `replayed: true` result proves the idempotency receipt — deliberately
    NOT excluded from a backup, unlike the rebuildable search index (see `backup/service.py`'s
    module docstring) — survived the round trip."""
    async with _mcp_session(server) as session:
        result = await session.call_tool(
            "pgos_create_node",
            {
                "workspace_id": workspace_id,
                "node_type_id": node_type_id,
                "title": "Acceptance MCP node",
                "actor_name": "system-pilot-fixture-agent",
                "reason": "functional fixture MCP coverage",
                "request_id": MCP_CREATE_NODE_REQUEST_ID,
            },
        )
        assert result.structuredContent is not None
        return bool(result.structuredContent["replayed"])


async def _materialize_context_pack(
    server: RunningServer, *, context_pack_id: str
) -> dict[str, Any]:
    async with _mcp_session(server) as session:
        result = await session.call_tool(
            "pgos_materialize_context_pack", {"context_pack_id": context_pack_id}
        )
        assert result.structuredContent is not None
        return dict(result.structuredContent)


@pytest.mark.slow
def test_recovery_drill_restores_identical_state_across_every_surface(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "original-workspace"
    server_a = RunningServer(workspace_dir / "graph.db", free_loopback_port())
    server_a.start()
    try:
        summary = build_functional_fixture(server_a.base_url, server_a.mcp_url, server_a.token)
        assert summary.export_verified is True

        before_snapshot = _canonical_snapshot(server_a, summary.workspace_id)
        before_attachment_hash = _attachment_hash(server_a, summary.attachment_id)
        # `summary.default_node_type_id`, not `before_snapshot["node_types"][0]["id"]`: the
        # API returns node types alphabetically by name, and the custom "Acceptance Type"
        # schema this fixture adds sorts before the defaults — index 0 after the fixture runs
        # is a different node type than the one the fixture's own MCP mutation actually used.
        before_replayed = asyncio.run(
            _replay_mcp_mutation(
                server_a,
                workspace_id=summary.workspace_id,
                node_type_id=summary.default_node_type_id,
            )
        )
        assert before_replayed is True
        before_pack = asyncio.run(
            _materialize_context_pack(server_a, context_pack_id=summary.context_pack_id)
        )
    finally:
        server_a.stop()

    backups_dir = tmp_path / "backups"
    backup_path = create_backup(workspace_dir, backups_dir)
    manifest = verify_backup(backup_path)
    assert manifest["format_version"] == "1"

    destination = tmp_path / "restored-workspace"
    assert not destination.exists()
    restore_backup(backup_path, destination)
    assert (destination / "graph.db").exists()
    assert not (destination / "api-token").exists()

    server_b = RunningServer(destination / "graph.db", free_loopback_port())
    server_b.start()
    try:
        assert server_b.token != server_a.token

        after_snapshot = _canonical_snapshot(server_b, summary.workspace_id)
        assert after_snapshot == before_snapshot

        after_attachment_hash = _attachment_hash(server_b, summary.attachment_id)
        assert after_attachment_hash == before_attachment_hash

        # Rebuilt search: `search_documents` is deliberately excluded from a backup (it is
        # rebuildable), so a non-empty hit here proves `create_app`'s startup backfill
        # actually rebuilt it from the restored canonical rows, not that it was carried over.
        with _rest_client(server_b) as client:
            search_results = client.get(
                "/search", params={"workspace_id": summary.workspace_id, "q": "acceptance"}
            ).json()
        assert len(search_results) > 0

        after_replayed = asyncio.run(
            _replay_mcp_mutation(
                server_b,
                workspace_id=summary.workspace_id,
                node_type_id=summary.default_node_type_id,
            )
        )
        assert after_replayed is True

        after_pack = asyncio.run(
            _materialize_context_pack(server_b, context_pack_id=summary.context_pack_id)
        )
        assert after_pack == before_pack

        # Export must also still work against the restored workspace.
        with _rest_client(server_b) as client:
            export_response = client.get("/export", params={"workspace_id": summary.workspace_id})
        export_response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(export_response.content)) as archive:
            assert "manifest.json" in archive.namelist()
    finally:
        server_b.stop()
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(backups_dir, ignore_errors=True)


@pytest.mark.slow
def test_restore_fails_closed_on_a_corrupted_backup_without_installing_a_destination(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "original-workspace"
    server = RunningServer(workspace_dir / "graph.db", free_loopback_port())
    server.start()
    try:
        build_functional_fixture(server.base_url, server.mcp_url, server.token)
    finally:
        server.stop()

    backup_path = create_backup(workspace_dir, tmp_path / "backups")

    # Flip one byte inside the zip's local-file data region (well past the end-of-central-
    # directory record) so the archive is still parseable as a zip but every entry's stored
    # checksum stops matching its content — the same corruption shape
    # `tests/backup/test_backup_service.py` already exercises at the unit level.
    corrupted_path = tmp_path / "corrupted.zip"
    shutil.copyfile(backup_path, corrupted_path)
    raw_bytes = bytearray(corrupted_path.read_bytes())
    flip_index = len(raw_bytes) // 2
    raw_bytes[flip_index] ^= 0xFF
    corrupted_path.write_bytes(bytes(raw_bytes))

    destination = tmp_path / "restored-from-corrupted"
    assert not destination.exists()
    with pytest.raises(BackupIntegrityError):
        restore_backup(corrupted_path, destination)

    # Fail-closed: no partially-installed destination, and no leftover staging directory.
    assert not destination.exists()
    staging_leftovers = list(tmp_path.glob(f".{destination.name}.pgos-restore-staging-*"))
    assert staging_leftovers == []
