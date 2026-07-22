from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import NodeService, new_workspace
from personal_graph_os.application.workflow_chain import (
    WorkflowChainService,
    WorkflowChainStep,
    WorkflowStepMismatchError,
)
from personal_graph_os.domain.errors import DomainError
from personal_graph_os.domain.identifiers import NodeId, new_id
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import SqliteResearchUnitOfWork


def _fixture(sqlite_connection: sqlite3.Connection):
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    node_service = NodeService(
        workspace_repository, node_repository, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )
    workflow_chain_service = WorkflowChainService(
        workspace_repository, node_repository, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )
    resource_type = workspace.node_type_by_system_key("resource")
    assert resource_type is not None
    resource_node = node_service.capture(workspace.id, resource_type.id, "A paper")
    return workflow_chain_service, node_repository, workspace, resource_node


def test_advance_creates_a_new_node_and_connecting_edge(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workflow_chain_service, node_repository, workspace, resource_node = _fixture(sqlite_connection)

    target_node, edge = workflow_chain_service.advance(
        workspace.id,
        resource_node.id,
        WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
        title="Key insight",
    )

    takeaway_type = workspace.node_type_by_system_key("takeaway")
    assert takeaway_type is not None
    assert target_node.node_type_id == takeaway_type.id
    assert target_node.title == "Key insight"
    assert edge.source_node_id == resource_node.id
    assert edge.target_node_id == target_node.id
    assert node_repository.get(target_node.id) is not None


def test_advance_connects_to_an_existing_node_instead_of_creating_one(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workflow_chain_service, node_repository, workspace, resource_node = _fixture(sqlite_connection)
    takeaway_type = workspace.node_type_by_system_key("takeaway")
    assert takeaway_type is not None
    existing_takeaway = NodeService(
        SqliteWorkspaceRepository(sqlite_connection),
        node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    ).capture(workspace.id, takeaway_type.id, "Existing takeaway")

    target_node, edge = workflow_chain_service.advance(
        workspace.id,
        resource_node.id,
        WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
        existing_target_node_id=existing_takeaway.id,
    )

    assert target_node.id == existing_takeaway.id
    assert edge.target_node_id == existing_takeaway.id


def test_advance_rejects_a_source_node_of_the_wrong_role(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workflow_chain_service, _node_repository, workspace, resource_node = _fixture(sqlite_connection)

    with pytest.raises(WorkflowStepMismatchError):
        workflow_chain_service.advance(
            workspace.id,
            resource_node.id,
            WorkflowChainStep.TAKEAWAY_TO_DECISION,
            title="Wrong step",
        )


def test_advance_rejects_an_existing_target_node_of_the_wrong_role(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workflow_chain_service, _node_repository, workspace, resource_node = _fixture(sqlite_connection)
    resource_type = workspace.node_type_by_system_key("resource")
    assert resource_type is not None
    other_resource = NodeService(
        SqliteWorkspaceRepository(sqlite_connection),
        _node_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    ).capture(workspace.id, resource_type.id, "Another resource")

    with pytest.raises(WorkflowStepMismatchError):
        workflow_chain_service.advance(
            workspace.id,
            resource_node.id,
            WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
            existing_target_node_id=other_resource.id,
        )


def test_advance_requires_either_a_title_or_an_existing_target(
    sqlite_connection: sqlite3.Connection,
) -> None:
    workflow_chain_service, _node_repository, workspace, resource_node = _fixture(sqlite_connection)

    with pytest.raises(DomainError):
        workflow_chain_service.advance(
            workspace.id, resource_node.id, WorkflowChainStep.RESOURCE_TO_TAKEAWAY
        )


def test_advance_rejects_an_unknown_source_node(sqlite_connection: sqlite3.Connection) -> None:
    workflow_chain_service, _node_repository, workspace, _resource_node = _fixture(
        sqlite_connection
    )

    with pytest.raises(DomainError):
        workflow_chain_service.advance(
            workspace.id,
            NodeId(new_id()),
            WorkflowChainStep.RESOURCE_TO_TAKEAWAY,
            title="x",
        )
