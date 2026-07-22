"""`AgentGatewayService`: the transport-neutral read boundary MCP tools call.

No handler in `infrastructure/mcp/server.py` talks to a repository or REST route directly;
every read goes through this service so REST and MCP stay behaviorally identical and the SDK
adapter never needs application/domain knowledge beyond this narrow surface.
"""

from __future__ import annotations

from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.repositories import (
    EdgeRepository,
    NodeRepository,
    ResourceRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.search_service import SearchService
from personal_graph_os.domain.identifiers import NodeId, ResourceId, WorkspaceId
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


class GatewayValidationError(ValueError):
    """A caller-supplied argument violates a bounded MCP tool contract (e.g. limit too high)."""


class GatewayNotFoundError(LookupError):
    """The referenced workspace/node/resource does not exist."""


def _validate_limit(limit: int, *, maximum: int) -> None:
    if limit < 1 or limit > maximum:
        raise GatewayValidationError(f"limit must be between 1 and {maximum}, got {limit}")


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
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._resources = resources
        self._search = search
        self._files = files

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
