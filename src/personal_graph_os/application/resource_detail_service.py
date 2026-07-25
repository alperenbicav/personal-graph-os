"""Read-only aggregate for one Resource's Research/Repository detail view (EP-2026-012 ST-06):
a single composed read across the current enrichment profile version, graph relations incident
to the resource's node, related Wiki documents, and ingestion-job provenance -- everything the
Research/Repositories master-detail panel needs in one round trip, mirroring bounded read-only
services elsewhere (`ResearchDashboardService`) rather than folding this into `ResourceService`'s
own create/update/archive responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass

from personal_graph_os.application.repositories import (
    DocumentLinkRepository,
    DocumentRepository,
    EdgeRepository,
    IngestionJobRepository,
    NodeRepository,
    ResourceEnrichmentProfileRepository,
    ResourceEnrichmentProfileVersionRepository,
    ResourceRepository,
    WorkItemRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.services import ResourceNotFoundError, WorkspaceNotFoundError
from personal_graph_os.domain.documents import DocumentLinkTargetType
from personal_graph_os.domain.enrichment import ResourceEnrichmentProfileVersion
from personal_graph_os.domain.identifiers import ResourceId
from personal_graph_os.domain.ingestion import IngestionJob
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.schema import Workspace

# The canonical domain a relation's other node belongs to (review finding S6-R03): a UI needs
# this, not just a node id, to route "related work"/"related resource" to its own canonical
# detail view rather than only a generic graph node.
RELATION_TARGET_RESOURCE = "resource"
RELATION_TARGET_WORK_ITEM = "work_item"
RELATION_TARGET_NODE = "node"


@dataclass(frozen=True)
class RelatedNode:
    edge_id: str
    direction: str  # "outgoing" | "incoming", relative to this resource's node
    edge_type_name: str
    node_id: str
    node_title: str
    # One of RELATION_TARGET_RESOURCE / RELATION_TARGET_WORK_ITEM / RELATION_TARGET_NODE.
    target_domain: str
    # The other node's canonical Resource/WorkItem id (for routing to its own detail view), or
    # `None` when target_domain is RELATION_TARGET_NODE (a generic graph-only node).
    target_entity_id: str | None
    resource_kind: str | None  # the other node's Resource.kind, or None if not a Resource


@dataclass(frozen=True)
class RelatedDocument:
    document_id: str
    title: str
    kind: str


@dataclass(frozen=True)
class ResourceDetail:
    resource: Resource
    node_title: str
    node_body: str
    enrichment: ResourceEnrichmentProfileVersion | None
    relations: tuple[RelatedNode, ...]
    related_documents: tuple[RelatedDocument, ...]
    provenance: IngestionJob | None


class ResourceDetailService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        resources: ResourceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        document_links: DocumentLinkRepository,
        documents: DocumentRepository,
        enrichment_profiles: ResourceEnrichmentProfileRepository,
        enrichment_profile_versions: ResourceEnrichmentProfileVersionRepository,
        ingestion_jobs: IngestionJobRepository,
        work_items: WorkItemRepository,
    ) -> None:
        self._workspaces = workspaces
        self._resources = resources
        self._nodes = nodes
        self._edges = edges
        self._document_links = document_links
        self._documents = documents
        self._enrichment_profiles = enrichment_profiles
        self._enrichment_profile_versions = enrichment_profile_versions
        self._ingestion_jobs = ingestion_jobs
        self._work_items = work_items

    def get_detail(self, resource_id: ResourceId) -> ResourceDetail:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        node = self._nodes.get(resource.node_id)
        if node is None:
            raise ResourceNotFoundError(
                f"resource {resource.id}'s backing node {resource.node_id} is missing"
            )
        workspace = self._workspaces.get(resource.workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {resource.workspace_id} does not exist")

        return ResourceDetail(
            resource=resource,
            node_title=node.title,
            node_body=node.body,
            enrichment=self._current_enrichment(resource),
            relations=self._relations(resource, workspace),
            related_documents=self._related_documents(resource),
            provenance=self._provenance(resource),
        )

    def _current_enrichment(self, resource: Resource) -> ResourceEnrichmentProfileVersion | None:
        profile = self._enrichment_profiles.get_by_identifier(
            resource.workspace_id, resource.canonical_identifier
        )
        if profile is None or profile.current_version_id is None:
            return None
        return self._enrichment_profile_versions.get(profile.current_version_id)

    def _relations(self, resource: Resource, workspace: Workspace) -> tuple[RelatedNode, ...]:
        related: list[RelatedNode] = []
        for edge in self._edges.list_incident_to_node(resource.node_id):
            is_outgoing = edge.source_node_id == resource.node_id
            other_node_id = edge.target_node_id if is_outgoing else edge.source_node_id
            other_node = self._nodes.get(other_node_id)
            if other_node is None:
                continue
            edge_type = workspace.edge_type_by_id(edge.edge_type_id)
            other_resource = self._resources.get_by_node(other_node_id)
            other_work_item = (
                None
                if other_resource is not None
                else (self._work_items.get_by_node(other_node_id))
            )
            if other_resource is not None:
                target_domain = RELATION_TARGET_RESOURCE
                target_entity_id = other_resource.id
                resource_kind = other_resource.kind.value
            elif other_work_item is not None:
                target_domain = RELATION_TARGET_WORK_ITEM
                target_entity_id = other_work_item.id
                resource_kind = None
            else:
                target_domain = RELATION_TARGET_NODE
                target_entity_id = None
                resource_kind = None
            related.append(
                RelatedNode(
                    edge_id=edge.id,
                    direction="outgoing" if is_outgoing else "incoming",
                    edge_type_name=edge_type.name if edge_type is not None else "unknown",
                    node_id=other_node.id,
                    node_title=other_node.title,
                    target_domain=target_domain,
                    target_entity_id=target_entity_id,
                    resource_kind=resource_kind,
                )
            )
        return tuple(related)

    def _related_documents(self, resource: Resource) -> tuple[RelatedDocument, ...]:
        links = self._document_links.list_by_target(DocumentLinkTargetType.NODE, resource.node_id)
        related: list[RelatedDocument] = []
        for link in links:
            document = self._documents.get(link.document_id)
            if document is None:
                continue
            related.append(
                RelatedDocument(
                    document_id=document.id, title=document.title, kind=document.kind.value
                )
            )
        return tuple(related)

    def _provenance(self, resource: Resource) -> IngestionJob | None:
        return self._ingestion_jobs.get_by_result_entity(
            resource.workspace_id, result_entity_type="resource", result_entity_id=resource.id
        )
