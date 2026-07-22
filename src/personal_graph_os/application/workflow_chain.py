"""The guided Resource -> Takeaway -> Decision -> Task -> Implementation workflow (ST-04.4).

Each step either connects an existing node of the right semantic role or creates a fresh one,
then links it to the prior step's node with the matching workflow edge type. Both writes commit
or roll back together via `ResearchUnitOfWork`, so a failure (e.g. an invalid title, or a
selected node of the wrong type) never leaves a new node stranded without the edge that makes it
part of the chain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from personal_graph_os.application.repositories import NodeRepository, WorkspaceRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.semantic_keys import (
    DECISION_NODE_TYPE_KEY,
    DECISION_PRODUCES_TASK_EDGE_KEY,
    IMPLEMENTATION_NODE_TYPE_KEY,
    RESOURCE_NODE_TYPE_KEY,
    RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY,
    TAKEAWAY_INFORMS_DECISION_EDGE_KEY,
    TAKEAWAY_NODE_TYPE_KEY,
    TASK_IMPLEMENTED_BY_EDGE_KEY,
    TASK_NODE_TYPE_KEY,
)
from personal_graph_os.domain.errors import DomainError, UnknownSchemaReferenceError
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId
from personal_graph_os.domain.schema import Workspace


@dataclass(frozen=True)
class WorkflowStepDefinition:
    from_node_type_key: str
    to_node_type_key: str
    edge_type_key: str


class WorkflowChainStep(StrEnum):
    """API/wire-safe step identifier. `WORKFLOW_CHAIN_STEP_DEFINITIONS` carries the actual
    node/edge-type roles each step connects, kept separate so this stays a plain string enum
    FastAPI/pydantic can serialize and validate directly."""

    RESOURCE_TO_TAKEAWAY = "resource_to_takeaway"
    TAKEAWAY_TO_DECISION = "takeaway_to_decision"
    DECISION_TO_TASK = "decision_to_task"
    TASK_TO_IMPLEMENTATION = "task_to_implementation"


WORKFLOW_CHAIN_STEP_DEFINITIONS: dict[WorkflowChainStep, WorkflowStepDefinition] = {
    WorkflowChainStep.RESOURCE_TO_TAKEAWAY: WorkflowStepDefinition(
        RESOURCE_NODE_TYPE_KEY, TAKEAWAY_NODE_TYPE_KEY, RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY
    ),
    WorkflowChainStep.TAKEAWAY_TO_DECISION: WorkflowStepDefinition(
        TAKEAWAY_NODE_TYPE_KEY, DECISION_NODE_TYPE_KEY, TAKEAWAY_INFORMS_DECISION_EDGE_KEY
    ),
    WorkflowChainStep.DECISION_TO_TASK: WorkflowStepDefinition(
        DECISION_NODE_TYPE_KEY, TASK_NODE_TYPE_KEY, DECISION_PRODUCES_TASK_EDGE_KEY
    ),
    WorkflowChainStep.TASK_TO_IMPLEMENTATION: WorkflowStepDefinition(
        TASK_NODE_TYPE_KEY, IMPLEMENTATION_NODE_TYPE_KEY, TASK_IMPLEMENTED_BY_EDGE_KEY
    ),
}


class WorkflowStepNodeTypeMissingError(UnknownSchemaReferenceError):
    """Raised when the workspace has no node/edge type carrying a step's required role.

    `ensure_semantic_schema()` guarantees these on every bootstrap; this only fires for a
    workspace that skipped it.
    """


class WorkflowStepMismatchError(DomainError):
    """Raised when the source or an explicitly selected target node has the wrong role for
    the requested chain step."""


class WorkflowChainService:
    """The only path through which the guided workflow chain advances by one step."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise UnknownSchemaReferenceError(f"workspace {workspace_id} does not exist")
        return workspace

    def _require_node(self, node_id: NodeId, workspace_id: WorkspaceId) -> Node:
        node = self._nodes.get(node_id)
        if node is None or node.workspace_id != workspace_id:
            raise UnknownSchemaReferenceError(
                f"node {node_id} does not exist in workspace {workspace_id}"
            )
        return node

    def advance(
        self,
        workspace_id: WorkspaceId,
        source_node_id: NodeId,
        step: WorkflowChainStep,
        *,
        title: str | None = None,
        existing_target_node_id: NodeId | None = None,
    ) -> tuple[Node, Edge]:
        """Connect `source_node_id` to a new-or-selected target node for `step`.

        Exactly one of `title` (create) or `existing_target_node_id` (select) must be given.
        """
        with self._unit_of_work_factory() as unit_of_work:
            return self.advance_within(
                unit_of_work,
                workspace_id,
                source_node_id,
                step,
                title=title,
                existing_target_node_id=existing_target_node_id,
            )

    def advance_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        source_node_id: NodeId,
        step: WorkflowChainStep,
        *,
        title: str | None = None,
        existing_target_node_id: NodeId | None = None,
    ) -> tuple[Node, Edge]:
        """Same validation/write as `advance`, into a caller-managed, already-open
        `unit_of_work` instead of committing on its own."""
        definition = WORKFLOW_CHAIN_STEP_DEFINITIONS[step]
        workspace = self._require_workspace(workspace_id)
        source_node = self._require_node(source_node_id, workspace_id)

        from_node_type = workspace.node_type_by_system_key(definition.from_node_type_key)
        to_node_type = workspace.node_type_by_system_key(definition.to_node_type_key)
        edge_type = workspace.edge_type_by_system_key(definition.edge_type_key)
        if from_node_type is None or to_node_type is None or edge_type is None:
            raise WorkflowStepNodeTypeMissingError(
                f"workspace {workspace_id} is missing a node/edge type for step {step.name}"
            )
        if source_node.node_type_id != from_node_type.id:
            raise WorkflowStepMismatchError(
                f"node {source_node_id} is not a '{definition.from_node_type_key}' node; "
                f"cannot start step {step.name} from it"
            )

        if existing_target_node_id is not None:
            target_node = self._require_node(existing_target_node_id, workspace_id)
            if target_node.node_type_id != to_node_type.id:
                raise WorkflowStepMismatchError(
                    f"node {existing_target_node_id} is not a '{definition.to_node_type_key}' "
                    f"node; cannot use it as the target of step {step.name}"
                )
            is_new_target = False
        else:
            if not title:
                raise DomainError(
                    f"step {step.name} requires either a title for a new node or an existing "
                    "target node id"
                )
            target_node = Node(workspace_id=workspace_id, node_type_id=to_node_type.id, title=title)
            target_node.validate_against(to_node_type)
            is_new_target = True

        edge = Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type.id,
            source_node_id=source_node.id,
            target_node_id=target_node.id,
        )
        if is_new_target:
            unit_of_work.nodes.save_without_commit(target_node)
        unit_of_work.edges.save_without_commit(edge)
        return target_node, edge
