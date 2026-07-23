from __future__ import annotations

import sqlite3

from personal_graph_os.application.activity_recording import (
    MAX_EVENT_SNAPSHOT_BYTES,
    MutationContext,
    record_activity_event,
)
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.activity import ActorKind, MutationAction
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _seed_workspace(sqlite_connection: sqlite3.Connection) -> WorkspaceId:
    workspace = new_workspace("Personal")
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    return workspace.id


def test_mutation_context_rest_is_a_fixed_human_local_actor() -> None:
    context = MutationContext.rest()
    assert context.actor_kind is ActorKind.HUMAN
    assert context.actor_name == "human/local-user/rest"
    assert context.source == "rest"
    assert context.reason is None


def test_record_activity_event_bounds_an_oversized_snapshot(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace_id = _seed_workspace(sqlite_connection)
    huge_state: dict[str, object] = {"body": "x" * (MAX_EVENT_SNAPSHOT_BYTES + 1)}
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        record_activity_event(
            unit_of_work,
            workspace_id=workspace_id,
            context=MutationContext.rest(),
            entity_type="node",
            entity_id="node-1",
            action=MutationAction.CREATED,
            after_state=huge_state,
        )
    rows = SqliteActivityEventRepository(sqlite_connection).list_by_workspace(
        workspace_id, limit=10
    )
    assert len(rows) == 1
    assert rows[0].after_state == {"_snapshot_omitted_oversized": True}


def test_record_activity_event_marks_node_and_resource_lifecycle_actions_undoable(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace_id = _seed_workspace(sqlite_connection)
    cases = (
        ("node", MutationAction.CREATED, True),
        ("node", MutationAction.UPDATED, True),
        ("node", MutationAction.ARCHIVED, True),
        ("node", MutationAction.RESTORED, True),
        ("resource", MutationAction.UPDATED, True),
        ("edge", MutationAction.CREATED, True),
        ("placement", MutationAction.CREATED, True),
        ("placement", MutationAction.UPDATED, True),
        ("saved_view", MutationAction.UPDATED, True),
        ("research_settings", MutationAction.UPDATED, True),
        ("edge", MutationAction.DELETED, False),
        ("saved_view", MutationAction.CREATED, False),
        ("saved_view", MutationAction.DELETED, False),
        ("node_type", MutationAction.UPDATED, False),
        ("discovery_run", MutationAction.CREATED, False),
        ("attachment", MutationAction.DELETED, False),
    )
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        for entity_type, action, _expected in cases:
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=MutationContext.rest(),
                entity_type=entity_type,
                entity_id=f"{entity_type}-{action.value}",
                action=action,
            )

    events = SqliteActivityEventRepository(sqlite_connection).list_by_workspace(
        workspace_id, limit=50
    )
    by_entity_id = {event.entity_id: event for event in events}
    for entity_type, action, expected in cases:
        entity_id = f"{entity_type}-{action.value}"
        assert by_entity_id[entity_id].is_undoable is expected, entity_id
        assert by_entity_id[entity_id].actor_name == "human/local-user/rest"
        assert by_entity_id[entity_id].source == "rest"
        assert by_entity_id[entity_id].request_id is None
