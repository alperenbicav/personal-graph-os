"""Builds the ST-09.1 functional fixture: one small workspace that exercises every
invariant the plan lists (custom schema, connected nodes, two canvases/placements, a
research resource with a discovery result, a managed attachment, a machine-relative
FileReference, saved/projection views, activity/undo, an MCP mutation with an idempotency
receipt, a Context Pack, and a portable export) purely through public REST/MCP endpoints.

`build_functional_fixture` takes a `base_url`/`token` pair rather than an in-process
`TestClient` because the MCP portion needs real HTTP/streamable-HTTP transport (see
`running_server.py`); this also lets the standalone harness (`scripts/system_pilot.py`)
reuse the exact same builder against a subprocess-spawned server.

The two MCP request ids below are fixed literals, not per-run-generated ones: ST-09.4's
recovery drill needs to replay the exact same idempotent MCP mutation after a backup/restore
round trip to prove the idempotency receipt survived, and re-fetch the exact same Context
Pack by id — both need a value this module and the drill agree on ahead of time.

ST09-F04: every write below is a checked REST/MCP call (`raise_for_status`/`structuredContent`
assertion), and every count in `FunctionalFixtureSummary` comes from an independent re-read of
real server state after the fixture is built — never a hardcoded literal or a truthy-JSON
assumption. A 4xx from any step raises immediately, failing the whole fixture build rather than
silently reporting success on error JSON (see `test_functional_fixture.py`'s regression test).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_ACCEPTANCE_MACHINE_NAME = "system-pilot-fixture-machine"
_ALREADY_REVERSED_DISABLED_REASON = "already_reversed"

MCP_CREATE_NODE_REQUEST_ID = "system-pilot-functional-fixture-mcp-node"
MCP_CONTEXT_PACK_REQUEST_ID = "system-pilot-functional-fixture-context-pack"


@dataclass(frozen=True)
class FunctionalFixtureSummary:
    """Counts (plus a handful of ids the ST-09.4 recovery drill needs to re-fetch the exact
    same records after a restore) — never titles, bodies, paths, or other fixture content.
    Every field is derived from an independent re-read of real server state, not a
    hardcoded literal (ST09-F04)."""

    workspace_id: str
    default_node_type_id: str
    node_count: int
    edge_count: int
    canvas_count: int
    placement_count: int
    resource_count: int
    discovery_run_id: str
    attachment_count: int
    attachment_id: str
    file_reference_count: int
    saved_view_count: int
    activity_event_count: int
    undo_verified: bool
    context_pack_count: int
    context_pack_id: str
    mcp_node_id: str
    mcp_mutation_replayed: bool
    export_verified: bool


def _post(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    response = client.post(path, **kwargs)
    response.raise_for_status()
    return response


def _patch(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    response = client.patch(path, **kwargs)
    response.raise_for_status()
    return response


def _get(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    response = client.get(path, **kwargs)
    response.raise_for_status()
    return response


@asynccontextmanager
async def _mcp_session(mcp_url: str, token: str):
    async with streamablehttp_client(mcp_url, headers={"Authorization": f"Bearer {token}"}) as (
        read,
        write,
        _unused_get_session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _rest_client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})


_CUSTOM_FIELD_NAME = "confidence"
_CUSTOM_STATUS_NAME = "acceptance_reviewed"
_CUSTOM_EDGE_TYPE_NAME = "acceptance_relates_to"
_CUSTOM_EDGE_INVERSE_NAME = "is_related_to_by_acceptance"


def _build_custom_schema(client: httpx.Client, workspace_id: str) -> tuple[str, str]:
    """Custom node type + field + status + edge type, all product-editable schema. Returns
    (custom_node_type_id, custom_edge_type_id)."""
    node_type = _post(
        client,
        "/node-types",
        json={"workspace_id": workspace_id, "name": "Acceptance Type", "icon": "flask"},
    ).json()
    _post(
        client,
        f"/node-types/{node_type['id']}/fields",
        json={
            "workspace_id": workspace_id,
            "name": _CUSTOM_FIELD_NAME,
            "field_type": "select",
            "select_options": ["low", "medium", "high"],
        },
    )
    _post(
        client,
        f"/node-types/{node_type['id']}/statuses",
        json={"workspace_id": workspace_id, "name": _CUSTOM_STATUS_NAME},
    )
    edge_type = _post(
        client,
        "/edge-types",
        json={
            "workspace_id": workspace_id,
            "name": _CUSTOM_EDGE_TYPE_NAME,
            "inverse_name": _CUSTOM_EDGE_INVERSE_NAME,
        },
    ).json()
    return node_type["id"], edge_type["id"]


def _verify_custom_schema(
    client: httpx.Client, workspace_id: str, custom_node_type_id: str, custom_edge_type_id: str
) -> None:
    """ST09-F04: independently re-read the workspace (a fresh GET, not the create responses
    above) and confirm the custom field/status/edge-type relationships actually persisted
    with their exact members, rather than trusting each create call's 2xx alone."""
    workspace = _get(client, "/workspace").json()
    assert workspace["id"] == workspace_id, workspace
    node_type = next(nt for nt in workspace["node_types"] if nt["id"] == custom_node_type_id)
    field_names = {field["name"] for field in node_type["field_definitions"]}
    assert _CUSTOM_FIELD_NAME in field_names, node_type
    confidence_field = next(
        field for field in node_type["field_definitions"] if field["name"] == _CUSTOM_FIELD_NAME
    )
    assert confidence_field["field_type"] == "select", confidence_field
    assert set(confidence_field["select_options"]) == {"low", "medium", "high"}, confidence_field
    status_names = {status["name"] for status in node_type["status_definitions"]}
    assert _CUSTOM_STATUS_NAME in status_names, node_type

    edge_type = next(et for et in workspace["edge_types"] if et["id"] == custom_edge_type_id)
    assert edge_type["name"] == _CUSTOM_EDGE_TYPE_NAME, edge_type
    assert edge_type["inverse_name"] == _CUSTOM_EDGE_INVERSE_NAME, edge_type


_EXPORT_FIXED_TOP_LEVEL_ENTRIES = frozenset(
    {"manifest.json", "workspace.json", "activity.json", "file_references.json", "attachments.json"}
)
_EXPORT_FORBIDDEN_JSON_FIELDS = frozenset({"absolute_path"})
_EXPORT_FORMAT_VERSION = "1"
_EXPORT_SCHEMA_VERSION = "1"
_EXPORT_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# A safe attachment path segment: no path separator (forward or back slash) and no control
# character. Rejecting "." and "." /".." explicitly (below) keeps this independent of
# whatever a permissive single-segment regex would otherwise accept (ST09-F04 round 4).
_EXPORT_SAFE_PATH_SEGMENT_PATTERN = re.compile(r"^[^/\\\x00-\x1f]+$")


def _iter_json_nodes(node: Any) -> Any:
    """Yield every dict/list node in a parsed JSON structure, depth-first, so a forbidden
    field can be found regardless of how deeply it's nested."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_json_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_nodes(item)


def _is_safe_attachment_entry_path(path: str) -> bool:
    """`attachments/<id>/<file>` with exactly two safe, non-empty, non-`.`/`..` segments —
    parsed by splitting on `/` and validating each segment explicitly, not a single
    permissive regex (ST09-F04 round 4: `attachments/../private-bytes` previously matched a
    regex whose `[^/]+` segments didn't exclude `.`/`..`/backslashes)."""
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "attachments":
        return False
    attachment_id, file_name = parts[1], parts[2]
    for segment in (attachment_id, file_name):
        if segment in (".", ".."):
            return False
        if not _EXPORT_SAFE_PATH_SEGMENT_PATTERN.match(segment):
            return False
    return True


def _is_well_formed_manifest_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    path, size_bytes, sha256_hex = entry.get("path"), entry.get("size_bytes"), entry.get("sha256")
    if not isinstance(path, str) or not path:
        return False
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        return False
    if not isinstance(sha256_hex, str) or not _EXPORT_SHA256_HEX_PATTERN.match(sha256_hex):
        return False
    return True


def _verify_export(export_content: bytes, token: str) -> bool:
    """ST09-F04 (round 4): every check below is independent of what the archive's own
    manifest claims — a self-consistent-but-tampered manifest that manifests an unexpected
    extra entry, a leaked secret/path field outside `file_references.json`, a traversal-like
    attachment name, or a duplicate ZIP/manifest entry must still fail here (three of these
    were reviewer-built probes that a prior version accepted)."""
    token_bytes = token.encode("utf-8")
    with zipfile.ZipFile(BytesIO(export_content)) as archive:
        raw_names = archive.namelist()
        # Reject a duplicate raw ZIP member name *before* any `set(...)` conversion would
        # silently absorb it (ST09-F04 round 4: `duplicate_member_accepted`).
        if len(raw_names) != len(set(raw_names)):
            return False
        names = set(raw_names)

        # Fixed allowlist, independent of the manifest: every entry must be a known
        # top-level file or a safe attachment path.
        for name in names:
            if name in _EXPORT_FIXED_TOP_LEVEL_ENTRIES:
                continue
            if _is_safe_attachment_entry_path(name):
                continue
            return False
        if not _EXPORT_FIXED_TOP_LEVEL_ENTRIES.issubset(names):
            return False

        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            return False
        if manifest.get("format_version") != _EXPORT_FORMAT_VERSION:
            return False
        if manifest.get("schema_version") != _EXPORT_SCHEMA_VERSION:
            return False
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            return False
        if not all(_is_well_formed_manifest_entry(entry) for entry in entries):
            return False

        # Reject a duplicate manifest path *before* any `set(...)` conversion would silently
        # absorb it (ST09-F04 round 4: `duplicate_manifest_record_accepted`).
        entry_paths = [entry["path"] for entry in entries]
        if len(entry_paths) != len(set(entry_paths)):
            return False
        manifested_paths = set(entry_paths)

        # The manifest must describe exactly the non-manifest entries actually present in
        # the zip — neither omitting one (undetected content) nor claiming an entry that
        # doesn't exist (the never-happens-in-practice half of this check, kept symmetric).
        if manifested_paths != (names - {"manifest.json"}):
            return False

        for entry in entries:
            data = archive.read(entry["path"])
            if len(data) != entry["size_bytes"]:
                return False
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                return False

        # Independent secret/path scan across every archive member, including the manifest
        # itself: a raw-byte token scan, plus (for every JSON file) a structural scan for any
        # forbidden field name anywhere in the parsed content — not just `absolute_path`
        # inside `file_references.json`.
        for name in names:
            data = archive.read(name)
            if token_bytes in data:
                return False
            if name.startswith("attachments/"):
                continue
            parsed = json.loads(data)
            for node in _iter_json_nodes(parsed):
                if _EXPORT_FORBIDDEN_JSON_FIELDS & node.keys():
                    return False

    return True


async def _mcp_mutation_and_context_pack(
    mcp_url: str, token: str, *, workspace_id: str, default_node_type_id: str
) -> tuple[bool, str, str, int]:
    async with _mcp_session(mcp_url, token) as session:
        created = await session.call_tool(
            "pgos_create_node",
            {
                "workspace_id": workspace_id,
                "node_type_id": default_node_type_id,
                "title": "Acceptance MCP node",
                "actor_name": "system-pilot-fixture-agent",
                "reason": "functional fixture MCP coverage",
                "request_id": MCP_CREATE_NODE_REQUEST_ID,
            },
        )
        assert created.structuredContent is not None, f"pgos_create_node failed: {created}"
        replay = await session.call_tool(
            "pgos_create_node",
            {
                "workspace_id": workspace_id,
                "node_type_id": default_node_type_id,
                "title": "Acceptance MCP node",
                "actor_name": "system-pilot-fixture-agent",
                "reason": "functional fixture MCP coverage",
                "request_id": MCP_CREATE_NODE_REQUEST_ID,
            },
        )
        assert replay.structuredContent is not None, f"pgos_create_node replay failed: {replay}"
        mcp_node_id = str(created.structuredContent["node"]["id"])

        pack_result = await session.call_tool(
            "pgos_create_context_pack",
            {
                "workspace_id": workspace_id,
                "name": "Acceptance context pack",
                "node_ids": [mcp_node_id],
                "inclusion_reasons": {mcp_node_id: "functional fixture coverage"},
                "actor_name": "system-pilot-fixture-agent",
                "reason": "functional fixture context pack coverage",
                "request_id": MCP_CONTEXT_PACK_REQUEST_ID,
            },
        )
        assert pack_result.structuredContent is not None, (
            f"pgos_create_context_pack failed: {pack_result}"
        )
        context_pack_id = str(pack_result.structuredContent["context_pack"]["id"])

        # Independent re-read (ST09-F04): re-fetch the pack through a separate MCP call
        # rather than trusting only the create response.
        fetched_pack = await session.call_tool(
            "pgos_get_context_pack", {"context_pack_id": context_pack_id}
        )
        assert fetched_pack.structuredContent is not None, (
            f"pgos_get_context_pack failed: {fetched_pack}"
        )
        assert fetched_pack.structuredContent["id"] == context_pack_id
        # Evidence/MCP linkage (ST09-F04): the re-fetched pack's own node selection actually
        # names the MCP-created node, not just an id that happens to match.
        assert mcp_node_id in fetched_pack.structuredContent["node_ids"], fetched_pack

        # Independent count (ST09-F04): a real `pgos_list_context_packs` read, not a
        # hardcoded `1` assumed from the create call succeeding once.
        listed_packs = await session.call_tool(
            "pgos_list_context_packs", {"workspace_id": workspace_id}
        )
        assert listed_packs.structuredContent is not None, (
            f"pgos_list_context_packs failed: {listed_packs}"
        )
        context_pack_count = len(listed_packs.structuredContent["context_packs"])

        return (
            bool(replay.structuredContent["replayed"]),
            context_pack_id,
            mcp_node_id,
            context_pack_count,
        )


def build_functional_fixture(base_url: str, mcp_url: str, token: str) -> FunctionalFixtureSummary:
    with _rest_client(base_url, token) as client:
        workspace = _get(client, "/workspace").json()
        workspace_id = workspace["id"]
        default_node_type_id = workspace["node_types"][0]["id"]

        custom_node_type_id, custom_edge_type_id = _build_custom_schema(client, workspace_id)
        _verify_custom_schema(client, workspace_id, custom_node_type_id, custom_edge_type_id)

        source_node = _post(
            client,
            "/nodes",
            json={
                "workspace_id": workspace_id,
                "node_type_id": default_node_type_id,
                "title": "Acceptance source node",
            },
        ).json()
        target_node = _post(
            client,
            "/nodes",
            json={
                "workspace_id": workspace_id,
                "node_type_id": custom_node_type_id,
                "title": "Acceptance target node",
            },
        ).json()
        _post(
            client,
            "/edges",
            json={
                "workspace_id": workspace_id,
                "edge_type_id": custom_edge_type_id,
                "source_node_id": source_node["id"],
                "target_node_id": target_node["id"],
            },
        )

        canvas_one = _post(
            client,
            "/canvases",
            json={"workspace_id": workspace_id, "name": "Acceptance canvas one"},
        ).json()
        canvas_two = _post(
            client,
            "/canvases",
            json={"workspace_id": workspace_id, "name": "Acceptance canvas two"},
        ).json()
        _post(
            client,
            f"/canvases/{canvas_one['id']}/placements",
            json={"node_id": source_node["id"], "position_x": 0, "position_y": 0},
        )
        _post(
            client,
            f"/canvases/{canvas_two['id']}/placements",
            json={"node_id": target_node["id"], "position_x": 10, "position_y": 10},
        )

        _post(
            client,
            "/resources",
            json={
                "workspace_id": workspace_id,
                "title": "Acceptance research resource",
                "raw_source": "https://example.com/acceptance-resource",
            },
        )

        discovery_run = _post(
            client,
            "/discovery/apply",
            json={
                "workspace_id": workspace_id,
                "agent_identity": "system-pilot-fixture",
                "instruction": "acceptance fixture import",
                "candidates": [
                    {
                        "identifier": "https://example.com/acceptance-discovery-candidate",
                        "title": "Acceptance discovery candidate",
                    }
                ],
            },
        ).json()
        assert len(discovery_run["candidates"]) == 1, discovery_run

        attachment = _post(
            client,
            f"/nodes/{source_node['id']}/attachments",
            files={"file": ("acceptance-note.txt", b"acceptance fixture content", "text/plain")},
        ).json()

        _post(
            client,
            f"/nodes/{target_node['id']}/file-references",
            json={
                "machine_name": _ACCEPTANCE_MACHINE_NAME,
                "relative_path": "docs/acceptance-fixture.md",
                "repository_name": "personal-graph-os",
            },
        )

        saved_view = _post(
            client,
            "/saved-views",
            json={
                "workspace_id": workspace_id,
                "name": "Acceptance saved view",
                "view_kind": "table",
                "query": {},
            },
        ).json()
        table_projection = _post(
            client,
            "/views/table",
            json={"workspace_id": workspace_id, "saved_view_id": saved_view["id"]},
        ).json()
        # Saved projection result (ST09-F04): the saved view actually evaluates to a real
        # projection containing this fixture's own nodes, not just a 2xx create/evaluate
        # response.
        projected_node_ids = {item["node"]["id"] for item in table_projection}
        assert source_node["id"] in projected_node_ids, table_projection
        assert target_node["id"] in projected_node_ids, table_projection

        _patch(client, f"/nodes/{source_node['id']}", json={"title": "Acceptance source (edited)"})
        events_page = _get(
            client, "/activity-events", params={"workspace_id": workspace_id, "limit": 100}
        ).json()
        update_event = next(
            event
            for event in events_page["events"]
            if event["entity_id"] == source_node["id"] and event["action"] == "updated"
        )
        undo_response = _post(
            client,
            f"/activity-events/{update_event['id']}/undo",
            params={"workspace_id": workspace_id},
            json={"reason": "system-pilot functional fixture verification"},
        ).json()
        assert undo_response["reverses_event_id"] == update_event["id"], undo_response

        # Independent re-read (ST09-F04): re-fetch the *original* event through a separate
        # GET rather than trusting only the undo response, and confirm the server durably
        # recorded it as reversed (not just that the undo call returned 200 once).
        reversed_event_detail = _get(
            client,
            f"/activity-events/{update_event['id']}",
            params={"workspace_id": workspace_id},
        ).json()
        assert reversed_event_detail["disabled_reason"] == _ALREADY_REVERSED_DISABLED_REASON, (
            reversed_event_detail
        )
        # Undo linkage (ST09-F04): confirm undo actually reverted the node's title, not just
        # that the server recorded the event as reversed.
        nodes_after_undo = _get(client, "/nodes", params={"workspace_id": workspace_id}).json()
        reverted_source_node = next(
            node for node in nodes_after_undo if node["id"] == source_node["id"]
        )
        assert reverted_source_node["title"] == "Acceptance source node", reverted_source_node

        mcp_mutation_replayed, context_pack_id, mcp_node_id, context_pack_count = asyncio.run(
            _mcp_mutation_and_context_pack(
                mcp_url,
                token,
                workspace_id=workspace_id,
                default_node_type_id=default_node_type_id,
            )
        )
        # MCP/REST linkage (ST09-F04): the MCP-created node is visible through the REST read
        # path too, not only through the MCP tool's own response.
        nodes_after_mcp = _get(client, "/nodes", params={"workspace_id": workspace_id}).json()
        mcp_node_via_rest = next(node for node in nodes_after_mcp if node["id"] == mcp_node_id)
        assert mcp_node_via_rest["title"] == "Acceptance MCP node", mcp_node_via_rest

        final_events_page = _get(
            client, "/activity-events", params={"workspace_id": workspace_id, "limit": 100}
        ).json()
        if final_events_page["next_cursor"] is not None:
            raise AssertionError(
                "functional fixture produced more activity events than the 100-event page "
                "limit can count — raise the limit or stop assuming a small fixed fixture size"
            )

        # Portable export (ST-09.4): proves the real `/export` path works end to end against
        # this fixture's data, not just that the fixture's own REST/MCP writes succeeded.
        # ST09-F04: full manifest allowlist/hash/path-secret verification, not just
        # presence of `manifest.json`.
        export_response = _get(client, "/export", params={"workspace_id": workspace_id})
        export_verified = _verify_export(export_response.content, token)

        # Every count below is a fresh, independent read of real server state (ST09-F04) —
        # never the hardcoded literals or truthy-JSON assumptions this replaced. Discovery
        # creates its own backing Resource+Node for each imported candidate (product
        # decision #1), so `resource_count`/`node_count` include it, not just the explicitly
        # REST/MCP-created ones.
        nodes = _get(client, "/nodes", params={"workspace_id": workspace_id}).json()
        edges = _get(client, "/edges", params={"workspace_id": workspace_id}).json()
        canvases = _get(client, "/canvases", params={"workspace_id": workspace_id}).json()
        placement_count = sum(
            len(_get(client, f"/canvases/{canvas['id']}/placements").json()) for canvas in canvases
        )
        resources = _get(client, "/resources", params={"workspace_id": workspace_id}).json()
        saved_views = _get(client, "/saved-views", params={"workspace_id": workspace_id}).json()
        attachments = _get(client, f"/nodes/{source_node['id']}/attachments").json()
        file_references = _get(client, f"/nodes/{target_node['id']}/file-references").json()

        return FunctionalFixtureSummary(
            workspace_id=workspace_id,
            default_node_type_id=default_node_type_id,
            node_count=len(nodes),
            edge_count=len(edges),
            canvas_count=len(canvases),
            placement_count=placement_count,
            resource_count=len(resources),
            discovery_run_id=str(discovery_run["id"]),
            attachment_count=len(attachments),
            attachment_id=str(attachment["id"]),
            file_reference_count=len(file_references),
            saved_view_count=len(saved_views),
            activity_event_count=len(final_events_page["events"]),
            undo_verified=True,
            context_pack_count=context_pack_count,
            context_pack_id=context_pack_id,
            mcp_node_id=mcp_node_id,
            mcp_mutation_replayed=mcp_mutation_replayed,
            export_verified=export_verified,
        )
