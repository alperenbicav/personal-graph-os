from __future__ import annotations

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.services import new_workspace


def test_seed_default_schema_populates_node_and_edge_types() -> None:
    workspace = new_workspace("Personal")
    seeded = seed_default_schema(workspace)

    node_type_names = {node_type.name for node_type in seeded.node_types}
    edge_type_names = {edge_type.name for edge_type in seeded.edge_types}

    assert {"Task", "Note", "Project", "Decision", "Resource", "Repository"} <= node_type_names
    assert {"relates_to", "supports", "cites"} <= edge_type_names


def test_seed_default_schema_is_a_no_op_for_already_configured_workspace() -> None:
    workspace = new_workspace("Personal")
    seeded_once = seed_default_schema(workspace)
    seeded_twice = seed_default_schema(seeded_once)

    assert seeded_twice.node_types == seeded_once.node_types
    assert seeded_twice.edge_types == seeded_once.edge_types
