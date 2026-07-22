"""Typed, JSON-friendly DTOs returned by MCP tools.

Kept separate from domain models so the wire contract (`pgos_*` tool outputs) can stay
stable even if internal domain models grow fields the protocol should not expose, and so
no domain object accidentally leaks a server-only detail (e.g. a managed storage path).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from personal_graph_os.application.discovery import DiscoveryCandidatePreview, DiscoveryPreview
from personal_graph_os.domain.activity import DiscoveredCandidate, DiscoveryRun
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.search import SearchResult


class WorkspaceDTO(BaseModel):
    id: str
    name: str
    node_type_count: int
    edge_type_count: int
    created_at: datetime

    @classmethod
    def from_domain(cls, workspace: Workspace) -> WorkspaceDTO:
        return cls(
            id=workspace.id,
            name=workspace.name,
            node_type_count=len(workspace.node_types),
            edge_type_count=len(workspace.edge_types),
            created_at=workspace.created_at,
        )


class NodeDTO(BaseModel):
    id: str
    workspace_id: str
    node_type_id: str
    title: str
    body: str
    status_id: str | None
    field_values: dict[str, object]
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, node: Node) -> NodeDTO:
        return cls(
            id=node.id,
            workspace_id=node.workspace_id,
            node_type_id=node.node_type_id,
            title=node.title,
            body=node.body,
            status_id=node.status_id,
            field_values=dict(node.field_values),
            is_archived=node.is_archived,
            created_at=node.created_at,
            updated_at=node.updated_at,
        )


class EdgeDTO(BaseModel):
    id: str
    workspace_id: str
    edge_type_id: str
    source_node_id: str
    target_node_id: str
    field_values: dict[str, object]
    created_at: datetime

    @classmethod
    def from_domain(cls, edge: Edge) -> EdgeDTO:
        return cls(
            id=edge.id,
            workspace_id=edge.workspace_id,
            edge_type_id=edge.edge_type_id,
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            field_values=dict(edge.field_values),
            created_at=edge.created_at,
        )


class ResourceDTO(BaseModel):
    id: str
    workspace_id: str
    node_id: str
    kind: str
    canonical_identifier: str
    source_url: str | None
    lifecycle_status: str
    next_action: str | None
    next_action_dismissed: bool
    open_questions: tuple[str, ...]
    takeaways: tuple[str, ...]
    progress_percent: int | None
    review_at: datetime | None
    last_activity_at: datetime

    @classmethod
    def from_domain(cls, resource: Resource) -> ResourceDTO:
        return cls(
            id=resource.id,
            workspace_id=resource.workspace_id,
            node_id=resource.node_id,
            kind=resource.kind.value,
            canonical_identifier=resource.canonical_identifier,
            source_url=resource.source_url,
            lifecycle_status=resource.lifecycle_status.value,
            next_action=resource.next_action,
            next_action_dismissed=resource.next_action_dismissed,
            open_questions=resource.open_questions,
            takeaways=resource.takeaways,
            progress_percent=resource.progress_percent,
            review_at=resource.review_at,
            last_activity_at=resource.last_activity_at,
        )


class SearchHitDTO(BaseModel):
    node: NodeDTO
    resource: ResourceDTO | None
    snippet: str

    @classmethod
    def from_domain(cls, result: SearchResult) -> SearchHitDTO:
        return cls(
            node=NodeDTO.from_domain(result.node),
            resource=ResourceDTO.from_domain(result.resource) if result.resource else None,
            snippet=result.snippet,
        )


class EvidencePointerDTO(BaseModel):
    """Privacy-safe evidence metadata: never a storage/absolute path or file bytes."""

    pointer: str
    kind: str
    display_name: str
    mime_type: str | None
    is_verified: bool | None
    created_at: datetime | None

    @classmethod
    def from_attachment(cls, attachment: Attachment) -> EvidencePointerDTO:
        return cls(
            pointer=f"attachment:{attachment.id}",
            kind="attachment",
            display_name=attachment.file_name,
            mime_type=attachment.mime_type,
            is_verified=None,
            created_at=attachment.created_at,
        )

    @classmethod
    def from_file_reference(cls, file_reference: FileReference) -> EvidencePointerDTO:
        display_name = file_reference.repository_name or file_reference.machine_name
        return cls(
            pointer=f"file-reference:{file_reference.id}",
            kind="file_reference",
            display_name=display_name,
            mime_type=None,
            is_verified=(
                file_reference.last_verified_at is not None and not file_reference.is_missing
            ),
            created_at=file_reference.last_verified_at,
        )


class DiscoveryCandidatePreviewDTO(BaseModel):
    identifier: str
    title: str
    kind: str | None
    description: str
    evidence: tuple[str, ...]
    canonical_identifier: str | None
    decision: str
    reason: str
    existing_resource_id: str | None
    duplicate_of_candidate_index: int | None

    @classmethod
    def from_domain(cls, preview: DiscoveryCandidatePreview) -> DiscoveryCandidatePreviewDTO:
        return cls(
            identifier=preview.candidate.identifier,
            title=preview.candidate.title,
            kind=preview.candidate.kind.value if preview.candidate.kind else None,
            description=preview.candidate.description,
            evidence=preview.candidate.evidence,
            canonical_identifier=preview.canonical_identifier,
            decision=preview.decision.value,
            reason=preview.reason,
            existing_resource_id=preview.existing_resource_id,
            duplicate_of_candidate_index=preview.duplicate_of_candidate_index,
        )


class DiscoveryPreviewDTO(BaseModel):
    instruction: str
    sources_searched: tuple[str, ...]
    filters_interpreted: dict[str, object]
    candidates: tuple[DiscoveryCandidatePreviewDTO, ...]

    @classmethod
    def from_domain(cls, preview: DiscoveryPreview) -> DiscoveryPreviewDTO:
        return cls(
            instruction=preview.instruction,
            sources_searched=preview.sources_searched,
            filters_interpreted=preview.filters_interpreted,
            candidates=tuple(
                DiscoveryCandidatePreviewDTO.from_domain(candidate)
                for candidate in preview.candidates
            ),
        )


class DiscoveredCandidateDTO(BaseModel):
    raw_identifier: str
    canonical_identifier: str | None
    title: str
    kind: str | None
    description: str
    evidence: tuple[str, ...]
    outcome: str
    reason: str | None
    existing_resource_id: str | None
    imported_node_id: str | None

    @classmethod
    def from_domain(cls, candidate: DiscoveredCandidate) -> DiscoveredCandidateDTO:
        return cls(
            raw_identifier=candidate.raw_identifier,
            canonical_identifier=candidate.canonical_identifier,
            title=candidate.title,
            kind=candidate.kind.value if candidate.kind else None,
            description=candidate.description,
            evidence=candidate.evidence,
            outcome=candidate.outcome.value,
            reason=candidate.reason,
            existing_resource_id=candidate.existing_resource_id,
            imported_node_id=candidate.imported_node_id,
        )


class DiscoveryRunDTO(BaseModel):
    id: str
    workspace_id: str
    agent_identity: str
    instruction: str
    sources_searched: tuple[str, ...]
    filters_interpreted: dict[str, object]
    candidates: tuple[DiscoveredCandidateDTO, ...]
    started_at: datetime
    completed_at: datetime | None
    imported_count: int
    skipped_count: int

    @classmethod
    def from_domain(cls, run: DiscoveryRun) -> DiscoveryRunDTO:
        return cls(
            id=run.id,
            workspace_id=run.workspace_id,
            agent_identity=run.agent_identity,
            instruction=run.instruction,
            sources_searched=run.sources_searched,
            filters_interpreted=run.filters_interpreted,
            candidates=tuple(
                DiscoveredCandidateDTO.from_domain(candidate) for candidate in run.candidates
            ),
            started_at=run.started_at,
            completed_at=run.completed_at,
            imported_count=run.imported_count,
            skipped_count=run.skipped_count,
        )
