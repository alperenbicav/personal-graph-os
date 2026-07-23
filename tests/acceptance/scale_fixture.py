"""Builds the ST-09.3 representative-scale fixture through the same public REST surface as
`functional_fixture.py`. This module only seeds data; timing (09.3) reads the fixture back
in a separate pass so seed cost never contaminates a product-latency measurement.

The representative counts below are fixed by the ST-09 plan; `build_representative_fixture`
never accepts an override so no caller can silently drift past the approved bounds. Tests
that need a cheap correctness check call `build_scale_fixture` directly with small counts.

Every count in the returned `ScaleFixtureSummary` — except `attachment_count`, see its field
docstring — comes from an independent `GET` against the same real server after seeding, never
from loop-iteration bookkeeping. A seeding loop's own counters cannot prove what the server
actually persisted (an edge post could fail silently, a create could turn out to record more
than one activity event, and so on); if that happened, the plan's fixed 2,000/4,000/500/250/
10,000/50 bounds could be silently exceeded — the exact "representative fixture exceeding the
fixed bounds" stop condition `WORK.md` names — while the fixture still reported success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

REPRESENTATIVE_NODE_COUNT = 2_000
REPRESENTATIVE_EDGE_COUNT = 4_000
REPRESENTATIVE_RESOURCE_COUNT = 500
REPRESENTATIVE_ACTIVE_PLACEMENT_COUNT = 250
REPRESENTATIVE_ACTIVITY_EVENT_COUNT = 10_000
REPRESENTATIVE_ATTACHMENT_COUNT = 50

_SYNTHETIC_ATTACHMENT_BYTES = b"0" * 4096
_ACTIVITY_EVENT_PAGE_SIZE = 500


@dataclass(frozen=True)
class ScaleFixtureSummary:
    """Counts only — never titles, bodies, or identifiers."""

    workspace_id: str
    node_count: int
    edge_count: int
    resource_count: int
    active_canvas_placement_count: int
    activity_event_count: int
    # Loop-counter, not independently re-verified: there is no workspace-level attachment
    # list endpoint, only `GET /nodes/{node_id}/attachments` per node. Each attachment in
    # this fixture targets a distinct node with no dedup/reuse path (unlike edges, which can
    # silently no-op on an existing pair) and every upload's response is checked below, so
    # the count this loop reports is what was actually accepted by the server.
    attachment_count: int


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


def _rest_client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})


def _count_activity_events(client: httpx.Client, workspace_id: str) -> int:
    """Pages through the full audit trail rather than trusting how many mutations the
    seeding loops below think they performed."""
    total = 0
    cursor: str | None = None
    while True:
        params: dict[str, object] = {
            "workspace_id": workspace_id,
            "limit": _ACTIVITY_EVENT_PAGE_SIZE,
        }
        if cursor is not None:
            params["cursor"] = cursor
        page = _get(client, "/activity-events", params=params).json()
        total += len(page["events"])
        cursor = page["next_cursor"]
        if cursor is None:
            return total


def build_scale_fixture(
    base_url: str,
    token: str,
    *,
    node_count: int,
    edge_count: int,
    resource_count: int,
    active_canvas_placement_count: int,
    activity_event_count: int,
    attachment_count: int,
) -> ScaleFixtureSummary:
    if resource_count > node_count:
        raise ValueError(
            "resource_count cannot exceed node_count: creating a Resource also creates its "
            "own backing Node (product decision #1), so node_count is the fixture's total "
            "graph node count and must already include the resource-backed ones"
        )
    if edge_count > node_count * (node_count - 1):
        raise ValueError("edge_count cannot exceed the number of distinct node pairs available")
    if active_canvas_placement_count > node_count:
        raise ValueError("active_canvas_placement_count cannot exceed node_count")
    if attachment_count > node_count:
        raise ValueError("attachment_count cannot exceed node_count")

    explicit_node_count = node_count - resource_count

    with _rest_client(base_url, token) as client:
        workspace = _get(client, "/workspace").json()
        workspace_id = workspace["id"]
        default_node_type_id = workspace["node_types"][0]["id"]
        edge_type_id = workspace["edge_types"][0]["id"]
        canvas = _post(
            client,
            "/canvases",
            json={"workspace_id": workspace_id, "name": "Scale fixture canvas"},
        ).json()

        # `node_ids` ends up covering the fixture's whole node population: `explicit_node_count`
        # plain nodes plus one backing node per resource created just below — never just the
        # explicit nodes, so edges/placements/attachments can land on either kind.
        node_ids = [
            _post(
                client,
                "/nodes",
                json={
                    "workspace_id": workspace_id,
                    "node_type_id": default_node_type_id,
                    "title": f"Scale fixture node {index}",
                },
            ).json()["id"]
            for index in range(explicit_node_count)
        ]

        for index in range(resource_count):
            resource = _post(
                client,
                "/resources",
                json={
                    "workspace_id": workspace_id,
                    "title": f"Scale fixture resource {index}",
                    "raw_source": f"https://example.com/scale-fixture-resource-{index}",
                },
            ).json()
            node_ids.append(resource["node_id"])

        for index in range(edge_count):
            source_id = node_ids[index % node_count]
            target_id = node_ids[(index + 1) % node_count]
            if source_id == target_id:
                continue
            _post(
                client,
                "/edges",
                json={
                    "workspace_id": workspace_id,
                    "edge_type_id": edge_type_id,
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                },
            )

        for index in range(active_canvas_placement_count):
            _post(
                client,
                f"/canvases/{canvas['id']}/placements",
                json={"node_id": node_ids[index], "position_x": float(index), "position_y": 0.0},
            )

        attachments_created = 0
        for index in range(attachment_count):
            _post(
                client,
                f"/nodes/{node_ids[index]}/attachments",
                files={
                    "file": (
                        f"scale-fixture-attachment-{index}.bin",
                        _SYNTHETIC_ATTACHMENT_BYTES,
                        "application/octet-stream",
                    )
                },
            )
            attachments_created += 1

        # Pad with node-title updates until the real, server-verified audit trail reaches
        # the target — never assume the seeding above produced exactly one event per call.
        events_so_far = _count_activity_events(client, workspace_id)
        remaining_events_needed = max(activity_event_count - events_so_far, 0)
        for index in range(remaining_events_needed):
            _patch(
                client,
                f"/nodes/{node_ids[index % node_count]}",
                json={"title": f"Scale fixture node {index % node_count} (padded update {index})"},
            )

        verified_node_count = len(
            _get(client, "/nodes", params={"workspace_id": workspace_id}).json()
        )
        verified_edge_count = len(
            _get(client, "/edges", params={"workspace_id": workspace_id}).json()
        )
        verified_resource_count = len(
            _get(client, "/resources", params={"workspace_id": workspace_id}).json()
        )
        verified_placement_count = len(_get(client, f"/canvases/{canvas['id']}/placements").json())
        verified_activity_event_count = _count_activity_events(client, workspace_id)

        return ScaleFixtureSummary(
            workspace_id=workspace_id,
            node_count=verified_node_count,
            edge_count=verified_edge_count,
            resource_count=verified_resource_count,
            active_canvas_placement_count=verified_placement_count,
            activity_event_count=verified_activity_event_count,
            attachment_count=attachments_created,
        )


def build_representative_fixture(base_url: str, token: str) -> ScaleFixtureSummary:
    """The one fixed representative dataset the ST-09 plan authorizes: 2,000 nodes, 4,000
    edges, 500 resources, 250 active-canvas placements, 10,000 total activity events, and
    50 synthetic 4 KiB attachments. Never call `build_scale_fixture` directly with larger
    counts — that would exceed the plan's approved bounds and requires a new plan, not a
    silent parameter change here.
    """
    return build_scale_fixture(
        base_url,
        token,
        node_count=REPRESENTATIVE_NODE_COUNT,
        edge_count=REPRESENTATIVE_EDGE_COUNT,
        resource_count=REPRESENTATIVE_RESOURCE_COUNT,
        active_canvas_placement_count=REPRESENTATIVE_ACTIVE_PLACEMENT_COUNT,
        activity_event_count=REPRESENTATIVE_ACTIVITY_EVENT_COUNT,
        attachment_count=REPRESENTATIVE_ATTACHMENT_COUNT,
    )
