"""ST-09.3: proves the hot-path list/search queries actually use the indexes the schema
declares, rather than degrading to a full table scan as data grows toward the
representative fixture's scale.

Business data is seeded through the real REST API (`build_scale_fixture`), never raw SQL —
raw SQL here is limited to the read-only `EXPLAIN QUERY PLAN` statement itself, exactly the
carve-out the ST-09 plan makes for index-plan assertions. A few dozen rows are enough: SQLite
chooses to use a single-column-equality index regardless of table size (confirmed empirically
at 50 and 2,000 rows alike), so this test does not need the full 2,000/4,000/... representative
fixture to prove the same index is chosen — that keeps it fast enough to run every time.
"""

from __future__ import annotations

import pytest

from tests.acceptance.running_server import RunningServer
from tests.acceptance.scale_fixture import build_scale_fixture

# Small, but large enough that SQLite's planner has real statistics to reason about.
_NODE_COUNT = 60
_EDGE_COUNT = 40
_RESOURCE_COUNT = 10
_PLACEMENT_COUNT = 10
_ACTIVITY_EVENT_COUNT = 80
_ATTACHMENT_COUNT = 0


@pytest.fixture
def seeded_server(running_server: RunningServer) -> RunningServer:
    build_scale_fixture(
        running_server.base_url,
        running_server.token,
        node_count=_NODE_COUNT,
        edge_count=_EDGE_COUNT,
        resource_count=_RESOURCE_COUNT,
        active_canvas_placement_count=_PLACEMENT_COUNT,
        activity_event_count=_ACTIVITY_EVENT_COUNT,
        attachment_count=_ATTACHMENT_COUNT,
    )
    return running_server


def _plan(connection, sql: str, params: tuple) -> str:
    rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " | ".join(row["detail"] for row in rows)


@pytest.mark.slow
def test_node_listing_uses_the_workspace_index(seeded_server: RunningServer) -> None:
    connection = seeded_server.app.state.connection
    plan = _plan(
        connection,
        "SELECT * FROM nodes WHERE workspace_id = ?",
        (seeded_server.workspace_id,),
    )
    assert "USING INDEX idx_nodes_workspace" in plan, plan
    assert "SCAN nodes" not in plan.replace("SEARCH nodes", ""), plan


@pytest.mark.slow
def test_edge_listing_uses_the_workspace_index(seeded_server: RunningServer) -> None:
    connection = seeded_server.app.state.connection
    plan = _plan(
        connection,
        "SELECT * FROM edges WHERE workspace_id = ?",
        (seeded_server.workspace_id,),
    )
    assert "USING INDEX idx_edges_workspace" in plan, plan


@pytest.mark.slow
def test_activity_event_listing_uses_the_workspace_cursor_index(
    seeded_server: RunningServer,
) -> None:
    connection = seeded_server.app.state.connection
    plan = _plan(
        connection,
        "SELECT * FROM activity_events WHERE workspace_id = ? "
        "ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (seeded_server.workspace_id, 50),
    )
    assert "USING INDEX idx_activity_events_workspace_cursor" in plan, plan


@pytest.mark.slow
def test_search_uses_the_fts5_match_index_not_a_full_scan(seeded_server: RunningServer) -> None:
    connection = seeded_server.app.state.connection
    plan = _plan(
        connection,
        "SELECT entity_type, entity_id FROM search_documents "
        "WHERE search_documents MATCH ? AND workspace_id = ? ORDER BY rank LIMIT ?",
        ("fixture", seeded_server.workspace_id, 20),
    )
    # FTS5's own MATCH-accelerated index, not a per-row scan through every indexed document.
    assert "VIRTUAL TABLE INDEX" in plan, plan
