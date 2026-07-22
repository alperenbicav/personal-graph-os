"""`AgentGatewayService`: the one application-layer dependency MCP tools call.

No handler in `infrastructure/mcp/server.py` talks to a repository, application service, or
REST route directly; every read and mutation goes through this service so REST and MCP stay
behaviorally identical and the SDK adapter never needs application/domain knowledge beyond
this narrow surface. Mutations reuse the `*_within` service paths and `ResearchUnitOfWork` so
canonical validation and atomic multi-table commit never fork from REST's.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.repositories import (
    EdgeRepository,
    NodeRepository,
    ResourceRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.application.services import EdgeService, NodeService, ResourceService
from personal_graph_os.application.workflow_chain import WorkflowChainService, WorkflowChainStep
from personal_graph_os.domain.activity import ActivityEvent, ActorKind, MutationAction
from personal_graph_os.domain.identifiers import (
    EdgeTypeId,
    NodeId,
    NodeTypeId,
    ResourceId,
    StatusDefinitionId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.infrastructure.mcp.dto import (
    EdgeDTO,
    EvidencePointerDTO,
    NodeDTO,
    ResourceDTO,
    SearchHitDTO,
    WorkspaceDTO,
)

MAX_LIST_LIMIT = 100
MAX_SEARCH_LIMIT = 50
MAX_ACTOR_NAME_LENGTH = 200
MAX_REASON_LENGTH = 1_000
MAX_REQUEST_ID_LENGTH = 200
MAX_TITLE_LENGTH = 300

_MCP_SOURCE = "mcp"


class GatewayValidationError(ValueError):
    """A caller-supplied argument violates a bounded MCP tool contract (e.g. limit too high)."""


class GatewayNotFoundError(LookupError):
    """The referenced workspace/node/resource does not exist."""


class GatewayConflictError(ValueError):
    """A concurrent request already claimed the same idempotency key with different content."""


def _validate_limit(limit: int, *, maximum: int) -> None:
    if limit < 1 or limit > maximum:
        raise GatewayValidationError(f"limit must be between 1 and {maximum}, got {limit}")


def _validate_bounded_text(value: str, *, field_name: str, maximum: int) -> str:
    stripped = value.strip()
    if not stripped:
        raise GatewayValidationError(f"{field_name} must not be empty")
    if len(stripped) > maximum:
        raise GatewayValidationError(f"{field_name} must not exceed {maximum} characters")
    return stripped


class AgentGatewayService:
    """Bounded, read-only projection of application services for the MCP adapter."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        resources: ResourceRepository,
        search: SearchService,
        files: FileService,
        *,
        node_service: NodeService,
        edge_service: EdgeService,
        resource_service: ResourceService,
        workflow_chain_service: WorkflowChainService,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._resources = resources
        self._search = search
        self._files = files
        self._node_service = node_service
        self._edge_service = edge_service
        self._resource_service = resource_service
        self._workflow_chain_service = workflow_chain_service
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> None:
        if self._workspaces.get(workspace_id) is None:
            raise GatewayNotFoundError(f"Workspace {workspace_id} does not exist")

    def get_workspace(self, workspace_id: WorkspaceId) -> WorkspaceDTO:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise GatewayNotFoundError(f"Workspace {workspace_id} does not exist")
        return WorkspaceDTO.from_domain(workspace)

    def list_nodes(
        self,
        workspace_id: WorkspaceId,
        *,
        include_archived: bool = False,
        limit: int = MAX_LIST_LIMIT,
    ) -> tuple[NodeDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        nodes = self._nodes.list_by_workspace(workspace_id, include_archived=include_archived)
        ordered = sorted(nodes, key=lambda node: (node.created_at, node.id))
        return tuple(NodeDTO.from_domain(node) for node in ordered[:limit])

    def get_node(self, node_id: NodeId) -> NodeDTO:
        node = self._nodes.get(node_id)
        if node is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        return NodeDTO.from_domain(node)

    def list_edges(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int = MAX_LIST_LIMIT,
    ) -> tuple[EdgeDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        edges = self._edges.list_by_workspace(workspace_id)
        ordered = sorted(edges, key=lambda edge: (edge.created_at, edge.id))
        return tuple(EdgeDTO.from_domain(edge) for edge in ordered[:limit])

    def search(
        self,
        workspace_id: WorkspaceId,
        query_text: str,
        *,
        include_archived: bool = False,
        limit: int = MAX_SEARCH_LIMIT,
    ) -> tuple[SearchHitDTO, ...]:
        _validate_limit(limit, maximum=MAX_SEARCH_LIMIT)
        self._require_workspace(workspace_id)
        results = self._search.search(
            workspace_id, query_text, limit=limit, include_archived=include_archived
        )
        return tuple(SearchHitDTO.from_domain(result) for result in results)

    def list_resources(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int = MAX_LIST_LIMIT,
    ) -> tuple[ResourceDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        resources = self._resources.list_by_workspace(workspace_id)
        ordered = sorted(resources, key=lambda resource: (resource.last_activity_at, resource.id))
        return tuple(ResourceDTO.from_domain(resource) for resource in ordered[:limit])

    def get_resource(self, resource_id: ResourceId) -> ResourceDTO:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise GatewayNotFoundError(f"Resource {resource_id} does not exist")
        return ResourceDTO.from_domain(resource)

    def list_node_evidence(
        self,
        node_id: NodeId,
        *,
        limit: int = MAX_LIST_LIMIT,
    ) -> tuple[EvidencePointerDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        if self._nodes.get(node_id) is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        attachments = self._files.list_attachments(node_id)
        file_references = self._files.list_file_references(node_id)
        pointers = [EvidencePointerDTO.from_attachment(a) for a in attachments]
        pointers.extend(EvidencePointerDTO.from_file_reference(fr) for fr in file_references)
        ordered = sorted(pointers, key=lambda pointer: pointer.pointer)
        return tuple(ordered[:limit])

    # -- Attributed, idempotent mutations (decisions #13/#14, `WORK.md`) --------------------

    def _validate_attribution(
        self, actor_name: str, reason: str, request_id: str
    ) -> tuple[str, str, str]:
        actor_name = _validate_bounded_text(
            actor_name, field_name="actor_name", maximum=MAX_ACTOR_NAME_LENGTH
        )
        reason = _validate_bounded_text(reason, field_name="reason", maximum=MAX_REASON_LENGTH)
        request_id = _validate_bounded_text(
            request_id, field_name="request_id", maximum=MAX_REQUEST_ID_LENGTH
        )
        return actor_name, reason, request_id

    def _find_replay(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        actor_name: str,
        request_id: str,
    ) -> ActivityEvent | None:
        return unit_of_work.activity_events.get_by_request(
            workspace_id, _MCP_SOURCE, actor_name, request_id
        )

    def _record_event(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        actor_name: str,
        reason: str,
        request_id: str,
        entity_type: str,
        entity_id: str,
        action: MutationAction,
    ) -> None:
        event = ActivityEvent(
            workspace_id=workspace_id,
            actor_kind=ActorKind.AGENT,
            actor_name=actor_name,
            source=_MCP_SOURCE,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            reason=reason,
            request_id=request_id,
        )
        try:
            unit_of_work.activity_events.save_without_commit(event)
        except sqlite3.IntegrityError as error:
            raise GatewayConflictError(
                f"request_id {request_id!r} was already used by actor {actor_name!r} "
                "with different content"
            ) from error

    def create_node(
        self,
        workspace_id: WorkspaceId,
        node_type_id: NodeTypeId,
        title: str,
        *,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"node_id": replay.entity_id, "replayed": True}
            node = self._node_service.capture_within(
                unit_of_work, workspace_id, node_type_id, title
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="node",
                entity_id=node.id,
                action=MutationAction.CREATED,
            )
        self._node_service.index_captured_node(node)
        return {"node": NodeDTO.from_domain(node).model_dump(mode="json"), "replayed": False}

    def update_node(
        self,
        node_id: NodeId,
        *,
        title: str | None,
        body: str | None,
        status_id: StatusDefinitionId | None,
        field_values: dict[str, object] | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if title is not None:
            title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        workspace_id = existing.workspace_id
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"node_id": replay.entity_id, "replayed": True}
            node = self._node_service.update_within(
                unit_of_work,
                node_id,
                title=title,
                body=body,
                status_id=status_id,
                field_values=field_values,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="node",
                entity_id=node.id,
                action=MutationAction.UPDATED,
            )
        self._node_service.index_captured_node(node)
        return {"node": NodeDTO.from_domain(node).model_dump(mode="json"), "replayed": False}

    def archive_node(
        self, node_id: NodeId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        workspace_id = existing.workspace_id
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"node_id": replay.entity_id, "replayed": True}
            node = self._node_service.archive_within(unit_of_work, node_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="node",
                entity_id=node.id,
                action=MutationAction.ARCHIVED,
            )
        return {"node": NodeDTO.from_domain(node).model_dump(mode="json"), "replayed": False}

    def connect_nodes(
        self,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        source_node_id: NodeId,
        target_node_id: NodeId,
        *,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"edge_id": replay.entity_id, "replayed": True}
            edge = self._edge_service.connect_within(
                unit_of_work, workspace_id, edge_type_id, source_node_id, target_node_id
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="edge",
                entity_id=edge.id,
                action=MutationAction.CREATED,
            )
        return {"edge": EdgeDTO.from_domain(edge).model_dump(mode="json"), "replayed": False}

    def create_or_reuse_resource(
        self,
        workspace_id: WorkspaceId,
        title: str,
        raw_source: str,
        *,
        kind: ResourceKind | None,
        body: str,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"resource_id": replay.entity_id, "replayed": True}
            resource, was_created, node = self._resource_service.create_or_reuse_within(
                unit_of_work, workspace_id, title, raw_source, kind=kind, body=body
            )
            if was_created:
                self._record_event(
                    unit_of_work,
                    workspace_id=workspace_id,
                    actor_name=actor_name,
                    reason=reason,
                    request_id=request_id,
                    entity_type="resource",
                    entity_id=resource.id,
                    action=MutationAction.CREATED,
                )
        if was_created and node is not None:
            self._resource_service.index_created_resource(resource, node)
        return {
            "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
            "was_created": was_created,
            "replayed": False,
        }

    def update_resource(
        self,
        resource_id: ResourceId,
        *,
        lifecycle_status: ResourceLifecycleStatus | None,
        next_action: str | None,
        clear_next_action: bool,
        next_action_dismissed: bool | None,
        open_questions: tuple[str, ...] | None,
        takeaways: tuple[str, ...] | None,
        progress_percent: int | None,
        clear_progress_percent: bool,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.resources.get(resource_id)
            if existing is None:
                raise GatewayNotFoundError(f"Resource {resource_id} does not exist")
            workspace_id = existing.workspace_id
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"resource_id": replay.entity_id, "replayed": True}
            resource, was_modified = self._resource_service.update_within(
                unit_of_work,
                resource_id,
                lifecycle_status=lifecycle_status,
                next_action=next_action,
                clear_next_action=clear_next_action,
                next_action_dismissed=next_action_dismissed,
                open_questions=open_questions,
                takeaways=takeaways,
                progress_percent=progress_percent,
                clear_progress_percent=clear_progress_percent,
            )
            if was_modified:
                self._record_event(
                    unit_of_work,
                    workspace_id=workspace_id,
                    actor_name=actor_name,
                    reason=reason,
                    request_id=request_id,
                    entity_type="resource",
                    entity_id=resource.id,
                    action=MutationAction.UPDATED,
                )
        if was_modified:
            self._resource_service.index_updated_resource(resource)
        return {
            "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
            "modified": was_modified,
            "replayed": False,
        }

    def archive_resource(
        self, resource_id: ResourceId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.resources.get(resource_id)
            if existing is None:
                raise GatewayNotFoundError(f"Resource {resource_id} does not exist")
            workspace_id = existing.workspace_id
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"resource_id": replay.entity_id, "replayed": True}
            resource, was_modified = self._resource_service.archive_within(
                unit_of_work, resource_id
            )
            if was_modified:
                self._record_event(
                    unit_of_work,
                    workspace_id=workspace_id,
                    actor_name=actor_name,
                    reason=reason,
                    request_id=request_id,
                    entity_type="resource",
                    entity_id=resource.id,
                    action=MutationAction.ARCHIVED,
                )
        if was_modified:
            self._resource_service.index_updated_resource(resource)
        return {
            "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
            "modified": was_modified,
            "replayed": False,
        }

    def advance_workflow(
        self,
        workspace_id: WorkspaceId,
        source_node_id: NodeId,
        step: WorkflowChainStep,
        *,
        title: str | None,
        existing_target_node_id: NodeId | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if title is not None:
            title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        with self._unit_of_work_factory() as unit_of_work:
            replay = self._find_replay(unit_of_work, workspace_id, actor_name, request_id)
            if replay is not None:
                return {"edge_id": replay.entity_id, "replayed": True}
            target_node, edge = self._workflow_chain_service.advance_within(
                unit_of_work,
                workspace_id,
                source_node_id,
                step,
                title=title,
                existing_target_node_id=existing_target_node_id,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="edge",
                entity_id=edge.id,
                action=MutationAction.CREATED,
            )
        return {
            "target_node": NodeDTO.from_domain(target_node).model_dump(mode="json"),
            "edge": EdgeDTO.from_domain(edge).model_dump(mode="json"),
            "replayed": False,
        }
