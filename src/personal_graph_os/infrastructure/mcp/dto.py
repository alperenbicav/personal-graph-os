"""Typed, JSON-friendly DTOs returned by MCP tools.

Kept separate from domain models so the wire contract (`pgos_*` tool outputs) can stay
stable even if internal domain models grow fields the protocol should not expose, and so
no domain object accidentally leaks a server-only detail (e.g. a managed storage path).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

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
