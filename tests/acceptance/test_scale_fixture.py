from __future__ import annotations

import httpx
import pytest

from tests.acceptance.running_server import RunningServer
from tests.acceptance.scale_fixture import (
    REPRESENTATIVE_ACTIVE_PLACEMENT_COUNT,
    REPRESENTATIVE_ACTIVITY_EVENT_COUNT,
    REPRESENTATIVE_ATTACHMENT_COUNT,
    REPRESENTATIVE_EDGE_COUNT,
    REPRESENTATIVE_NODE_COUNT,
    REPRESENTATIVE_RESOURCE_COUNT,
    build_representative_fixture,
    build_scale_fixture,
)


def _independent_recheck(server: RunningServer, workspace_id: str) -> tuple[int, int, int]:
    """Queries the server again, from the test itself, with a fresh client the fixture
    module never touches — so this cannot pass merely because the summary agrees with
    itself. Only node/edge/resource are worth re-querying here: `build_scale_fixture`
    already derives its own summary from these exact endpoints (not from loop counters),
    so this is a genuine second, independent read of server state, not a second layer of
    the same arithmetic."""
    with httpx.Client(
        base_url=server.base_url, headers={"Authorization": f"Bearer {server.token}"}
    ) as client:
        nodes = client.get("/nodes", params={"workspace_id": workspace_id})
        edges = client.get("/edges", params={"workspace_id": workspace_id})
        resources = client.get("/resources", params={"workspace_id": workspace_id})
        for response in (nodes, edges, resources):
            response.raise_for_status()
        return len(nodes.json()), len(edges.json()), len(resources.json())


def test_build_scale_fixture_at_a_small_bounded_size(running_server: RunningServer) -> None:
    """A cheap correctness check at a tiny fraction of the representative counts — the full
    2,000/4,000/... representative dataset is exercised only by the opt-in `slow` test
    below and by `scripts/system_pilot.py`'s `performance` phase (09.3), never by the
    default suite."""
    summary = build_scale_fixture(
        running_server.base_url,
        running_server.token,
        node_count=20,
        edge_count=15,
        resource_count=5,
        active_canvas_placement_count=5,
        activity_event_count=50,
        attachment_count=3,
    )

    assert summary.node_count == 20
    assert summary.edge_count == 15
    assert summary.resource_count == 5
    assert summary.active_canvas_placement_count == 5
    assert summary.attachment_count == 3
    assert summary.activity_event_count == 50

    recheck_nodes, recheck_edges, recheck_resources = _independent_recheck(
        running_server, summary.workspace_id
    )
    assert recheck_nodes == summary.node_count
    assert recheck_edges == summary.edge_count
    assert recheck_resources == summary.resource_count


@pytest.mark.slow
def test_build_representative_fixture_matches_the_fixed_plan_bounds(
    running_server: RunningServer,
) -> None:
    """Opt-in only (`uv run pytest -m slow tests/acceptance`, or
    `scripts/system_pilot.py --phase functional --with-representative-fixture`): building
    the full representative dataset over real REST round trips takes minutes, so it never
    runs in the default `uv run pytest` suite."""
    summary = build_representative_fixture(running_server.base_url, running_server.token)

    assert summary.node_count == REPRESENTATIVE_NODE_COUNT
    assert summary.edge_count == REPRESENTATIVE_EDGE_COUNT
    assert summary.resource_count == REPRESENTATIVE_RESOURCE_COUNT
    assert summary.active_canvas_placement_count == REPRESENTATIVE_ACTIVE_PLACEMENT_COUNT
    assert summary.attachment_count == REPRESENTATIVE_ATTACHMENT_COUNT
    assert summary.activity_event_count == REPRESENTATIVE_ACTIVITY_EVENT_COUNT

    recheck_nodes, recheck_edges, recheck_resources = _independent_recheck(
        running_server, summary.workspace_id
    )
    assert recheck_nodes == REPRESENTATIVE_NODE_COUNT
    assert recheck_edges == REPRESENTATIVE_EDGE_COUNT
    assert recheck_resources == REPRESENTATIVE_RESOURCE_COUNT
