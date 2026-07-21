from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import NodeId, new_id
from personal_graph_os.domain.resource import Resource, ResourceKind
from personal_graph_os.domain.schema import EdgeType, NodeType, Workspace
from personal_graph_os.infrastructure.sqlite.repositories import SqliteWorkspaceRepository
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _seed_workspace(sqlite_connection: sqlite3.Connection) -> tuple[Workspace, NodeType]:
    resource_type = NodeType(name="Resource")
    workspace = Workspace(name="Personal", node_types=(resource_type,))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    return workspace, resource_type


def test_unit_of_work_commits_node_and_resource_together(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workspace, resource_type = _seed_workspace(sqlite_connection)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        node = Node(workspace_id=workspace.id, node_type_id=resource_type.id, title="A paper")
        unit_of_work.nodes.save_without_commit(node)
        resource = Resource(
            workspace_id=workspace.id,
            node_id=node.id,
            kind=ResourceKind.PAPER,
            canonical_identifier="arxiv:1",
        )
        unit_of_work.resources.save_without_commit(resource)

    assert unit_of_work.nodes.get(node.id) is not None
    assert unit_of_work.resources.get(resource.id) is not None


def test_unit_of_work_rolls_back_the_node_write_when_the_resource_write_fails(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """If the second table's write fails, the first table's write inside the same unit of
    work must not persist either — that is the entire point of the atomic boundary."""
    workspace, resource_type = _seed_workspace(sqlite_connection)
    node = Node(workspace_id=workspace.id, node_type_id=resource_type.id, title="A paper")

    with pytest.raises(sqlite3.IntegrityError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            unit_of_work.nodes.save_without_commit(node)
            # node_id has no matching workspace/kind constraints satisfied intentionally
            # broken here: violate the resources.node_id UNIQUE+NOT NULL/FK contract by
            # reusing a node id that does not exist in `nodes` for a *different* connection
            # state — simplest reliable failure is a duplicate canonical_identifier clash.
            first = Resource(
                workspace_id=workspace.id,
                node_id=node.id,
                kind=ResourceKind.PAPER,
                canonical_identifier="dup",
            )
            unit_of_work.resources.save_without_commit(first)
            second_node = Node(
                workspace_id=workspace.id, node_type_id=resource_type.id, title="Another"
            )
            unit_of_work.nodes.save_without_commit(second_node)
            second = Resource(
                workspace_id=workspace.id,
                node_id=second_node.id,
                kind=ResourceKind.PAPER,
                canonical_identifier="dup",
            )
            unit_of_work.resources.save_without_commit(second)

    readonly_nodes = sqlite_connection.execute("SELECT id FROM nodes").fetchall()
    assert readonly_nodes == []
    readonly_resources = sqlite_connection.execute("SELECT id FROM resources").fetchall()
    assert readonly_resources == []


def test_savepoint_isolates_one_candidate_failure_from_prior_successful_candidates(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A batch-import loop wraps each candidate in `savepoint()`; one candidate's failure
    must not undo the candidates already committed earlier in the same unit of work."""
    workspace, resource_type = _seed_workspace(sqlite_connection)

    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        good_node = Node(workspace_id=workspace.id, node_type_id=resource_type.id, title="Good")
        with unit_of_work.savepoint():
            unit_of_work.nodes.save_without_commit(good_node)
            unit_of_work.resources.save_without_commit(
                Resource(
                    workspace_id=workspace.id,
                    node_id=good_node.id,
                    kind=ResourceKind.PAPER,
                    canonical_identifier="good",
                )
            )

        with pytest.raises(sqlite3.IntegrityError):
            with unit_of_work.savepoint():
                bad_node = Node(
                    workspace_id=workspace.id, node_type_id=resource_type.id, title="Bad"
                )
                unit_of_work.nodes.save_without_commit(bad_node)
                unit_of_work.resources.save_without_commit(
                    Resource(
                        workspace_id=workspace.id,
                        node_id=bad_node.id,
                        kind=ResourceKind.PAPER,
                        canonical_identifier="good",  # duplicate -> violates uniqueness
                    )
                )

    node_titles = {row[0] for row in sqlite_connection.execute("SELECT title FROM nodes")}
    resource_identifiers = {
        row[0] for row in sqlite_connection.execute("SELECT canonical_identifier FROM resources")
    }
    assert node_titles == {"Good"}
    assert resource_identifiers == {"good"}


def test_unit_of_work_rolls_back_a_new_node_when_its_connecting_edge_write_fails(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """The guided workflow chain (ST-04.4) writes a new node and the edge connecting it in
    one unit of work; if the edge write fails, the node must not be left stranded either."""
    workspace, resource_type = _seed_workspace(sqlite_connection)
    edge_type = EdgeType(name="Yields")
    SqliteWorkspaceRepository(sqlite_connection).save(
        workspace.model_copy(update={"edge_types": (edge_type,)})
    )

    with pytest.raises(sqlite3.IntegrityError):
        with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
            new_node = Node(workspace_id=workspace.id, node_type_id=resource_type.id, title="New")
            unit_of_work.nodes.save_without_commit(new_node)
            unit_of_work.edges.save_without_commit(
                Edge(
                    workspace_id=workspace.id,
                    edge_type_id=edge_type.id,
                    source_node_id=new_node.id,
                    # A target node id that was never written anywhere violates the edges
                    # table's foreign key, forcing this write to fail inside the transaction.
                    target_node_id=NodeId(new_id()),
                )
            )

    readonly_nodes = sqlite_connection.execute("SELECT id FROM nodes").fetchall()
    assert readonly_nodes == []
    readonly_edges = sqlite_connection.execute("SELECT id FROM edges").fetchall()
    assert readonly_edges == []
