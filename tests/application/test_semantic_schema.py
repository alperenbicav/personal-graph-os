from __future__ import annotations

import pytest

from personal_graph_os.application.semantic_keys import (
    RESOURCE_NODE_TYPE_KEY,
    RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY,
    TASK_NODE_TYPE_KEY,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.errors import SchemaEditConflictError
from personal_graph_os.domain.schema import EdgeType, NodeType


def test_ensure_semantic_schema_creates_every_missing_role_on_a_bare_workspace() -> None:
    workspace = new_workspace("Personal")

    ensured = ensure_semantic_schema(workspace)

    keys = {nt.system_key for nt in ensured.node_types}
    assert {"resource", "takeaway", "decision", "task", "implementation"} <= keys
    edge_keys = {et.system_key for et in ensured.edge_types}
    assert {
        "resource_yields_takeaway",
        "takeaway_informs_decision",
        "decision_produces_task",
        "task_implemented_by",
    } <= edge_keys


def test_ensure_semantic_schema_adopts_a_pre_st04_node_type_by_name_without_losing_edits() -> None:
    """An older workspace's `Task` node type (created before `system_key` existed) has a
    user-added field; adoption must add only the role, not reset anything the user edited."""
    user_edited_task = NodeType(name="Task", icon="rocket", color_hex="#ff00ff")
    workspace = new_workspace("Personal").model_copy(update={"node_types": (user_edited_task,)})

    ensured = ensure_semantic_schema(workspace)

    adopted = ensured.node_type_by_system_key(TASK_NODE_TYPE_KEY)
    assert adopted is not None
    assert adopted.id == user_edited_task.id
    assert adopted.icon == "rocket"
    assert adopted.color_hex == "#ff00ff"


def test_ensure_semantic_schema_is_idempotent() -> None:
    workspace = new_workspace("Personal")
    ensured_once = ensure_semantic_schema(workspace)

    ensured_twice = ensure_semantic_schema(ensured_once)

    assert ensured_twice.node_types == ensured_once.node_types
    assert ensured_twice.edge_types == ensured_once.edge_types


def test_ensure_semantic_schema_never_reassigns_an_already_stamped_role() -> None:
    workspace = new_workspace("Personal").model_copy(
        update={
            "node_types": (
                NodeType(name="Renamed Resource", system_key=RESOURCE_NODE_TYPE_KEY),
                NodeType(name="Resource"),  # an unrelated later node type reusing the old name
            )
        }
    )

    ensured = ensure_semantic_schema(workspace)

    resource_role_holders = [
        nt for nt in ensured.node_types if nt.system_key == RESOURCE_NODE_TYPE_KEY
    ]
    assert len(resource_role_holders) == 1
    assert resource_role_holders[0].name == "Renamed Resource"


def test_ensure_semantic_schema_reports_a_genuine_role_collision() -> None:
    """A node type named 'Task' already claimed by a different role cannot be silently
    remapped to the 'task' role; this must surface as a documented conflict."""
    conflicting = NodeType(name="Task", system_key="some_other_role")
    workspace = new_workspace("Personal").model_copy(update={"node_types": (conflicting,)})

    with pytest.raises(SchemaEditConflictError):
        ensure_semantic_schema(workspace)


def test_ensure_semantic_schema_adopts_a_pre_st04_edge_type_by_name() -> None:
    user_edge_type = EdgeType(name="yields_takeaway", color_hex="#123456")
    workspace = new_workspace("Personal").model_copy(update={"edge_types": (user_edge_type,)})

    ensured = ensure_semantic_schema(workspace)

    adopted = ensured.edge_type_by_system_key(RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY)
    assert adopted is not None
    assert adopted.id == user_edge_type.id
    assert adopted.color_hex == "#123456"
