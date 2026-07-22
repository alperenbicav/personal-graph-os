"""Request DTOs for the HTTP API.

Domain models (`Node`, `Edge`, `Canvas`, `CanvasPlacement`, `Workspace`) are already typed
Pydantic models and are returned directly as response bodies. Requests need their own shape
because a capture/update/move is a partial, caller-supplied input, not a full entity.

`ResourceResponse` is the one response DTO in this module: a `Resource` alone would omit its
backing `Node`'s title/body, and product decision #2 (`WORK.md`) forbids giving `Resource` its
own separate copy of them.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, StrictInt

from personal_graph_os.application.projections import ProjectionItem
from personal_graph_os.application.workflow_chain import WorkflowChainStep
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.resource import Resource, ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.schema import FieldType
from personal_graph_os.domain.views import FilterField, ProjectionQuery, ViewKind

_MAX_DISCOVERY_CANDIDATES = 50
_MAX_DISCOVERY_TEXT_LENGTH = 4000
_MAX_DISCOVERY_TITLE_LENGTH = 300


class CaptureNodeRequest(BaseModel):
    workspace_id: str
    node_type_id: str
    title: str


class UpdateNodeRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    status_id: str | None = None
    field_values: dict[str, object] | None = None


class ConnectEdgeRequest(BaseModel):
    workspace_id: str
    edge_type_id: str
    source_node_id: str
    target_node_id: str


class CreateCanvasRequest(BaseModel):
    workspace_id: str
    name: str


class PlaceNodeRequest(BaseModel):
    node_id: str
    position_x: float
    position_y: float


class UpdatePlacementRequest(BaseModel):
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = None
    height: float | None = None
    is_collapsed: bool | None = None


class CreateFileReferenceRequest(BaseModel):
    machine_name: str
    relative_path: str
    repository_name: str | None = None
    absolute_path: str | None = None
    git_ref: str | None = None


class CreateNodeTypeRequest(BaseModel):
    workspace_id: str
    name: str
    icon: str = "circle"
    color_hex: str = "#6b7280"


class UpdateNodeTypeRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    icon: str | None = None
    color_hex: str | None = None


class CreateFieldDefinitionRequest(BaseModel):
    workspace_id: str
    name: str
    field_type: FieldType
    is_required: bool = False
    select_options: list[str] = []
    description: str | None = None


class UpdateFieldDefinitionRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    field_type: FieldType | None = None
    is_required: bool | None = None
    select_options: list[str] | None = None
    description: str | None = None
    clear_description: bool = False


class CreateStatusDefinitionRequest(BaseModel):
    workspace_id: str
    name: str
    color_hex: str = "#6b7280"
    is_terminal: bool = False
    sort_order: int = 0


class UpdateStatusDefinitionRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    color_hex: str | None = None
    is_terminal: bool | None = None
    sort_order: int | None = None


class CreateEdgeTypeRequest(BaseModel):
    workspace_id: str
    name: str
    inverse_name: str | None = None
    color_hex: str = "#6b7280"


class UpdateEdgeTypeRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    inverse_name: str | None = None
    clear_inverse_name: bool = False
    color_hex: str | None = None


class CreateOrReuseResourceRequest(BaseModel):
    workspace_id: str
    title: str
    raw_source: str
    kind: ResourceKind | None = None
    body: str = ""


class UpdateResourceRequest(BaseModel):
    lifecycle_status: ResourceLifecycleStatus | None = None
    next_action: str | None = None
    clear_next_action: bool = False
    next_action_dismissed: bool | None = None
    open_questions: list[str] | None = None
    takeaways: list[str] | None = None
    progress_percent: StrictInt | None = Field(default=None, ge=0, le=100)
    clear_progress_percent: bool = False
    review_at: datetime | None = None
    clear_review_at: bool = False


class ResourceResponse(BaseModel):
    id: str
    workspace_id: str
    node_id: str
    kind: ResourceKind
    canonical_identifier: str
    source_url: str | None
    lifecycle_status: ResourceLifecycleStatus
    next_action: str | None
    next_action_dismissed: bool
    open_questions: list[str]
    takeaways: list[str]
    progress_percent: int | None
    review_at: datetime | None
    last_activity_at: datetime
    title: str
    body: str

    @classmethod
    def from_resource_and_node(cls, resource: Resource, node: Node) -> ResourceResponse:
        return cls(
            id=resource.id,
            workspace_id=resource.workspace_id,
            node_id=resource.node_id,
            kind=resource.kind,
            canonical_identifier=resource.canonical_identifier,
            source_url=resource.source_url,
            lifecycle_status=resource.lifecycle_status,
            next_action=resource.next_action,
            next_action_dismissed=resource.next_action_dismissed,
            open_questions=list(resource.open_questions),
            takeaways=list(resource.takeaways),
            progress_percent=resource.progress_percent,
            review_at=resource.review_at,
            last_activity_at=resource.last_activity_at,
            title=node.title,
            body=node.body,
        )


class CreateSavedViewRequest(BaseModel):
    workspace_id: str
    name: str
    view_kind: ViewKind
    query: ProjectionQuery = ProjectionQuery()


class UpdateSavedViewRequest(BaseModel):
    name: str | None = None
    query: ProjectionQuery | None = None


class TableViewRequest(BaseModel):
    workspace_id: str
    query: ProjectionQuery = ProjectionQuery()
    saved_view_id: str | None = None


class KanbanViewRequest(BaseModel):
    workspace_id: str
    query: ProjectionQuery = ProjectionQuery()
    saved_view_id: str | None = None
    group_by: FilterField = FilterField.STATUS_ID


class TimelineViewRequest(BaseModel):
    workspace_id: str
    query: ProjectionQuery = ProjectionQuery()
    saved_view_id: str | None = None
    date_field: FilterField = FilterField.CREATED_AT


class ProjectionItemResponse(BaseModel):
    node: Node
    resource: Resource | None

    @classmethod
    def from_item(cls, item: ProjectionItem) -> ProjectionItemResponse:
        return cls(node=item.node, resource=item.resource)


class UpdateResearchSettingsRequest(BaseModel):
    stale_after_days: int


class ResearchDashboardResponse(BaseModel):
    inbox: list[ResourceResponse]
    continue_reading: list[ResourceResponse]
    stale: list[ResourceResponse]
    needs_takeaway: list[ResourceResponse]
    unlinked: list[ResourceResponse]
    applied: list[ResourceResponse]


class AdvanceWorkflowChainRequest(BaseModel):
    workspace_id: str
    source_node_id: str
    step: WorkflowChainStep
    title: str | None = None
    existing_target_node_id: str | None = None


class WorkflowChainStepResponse(BaseModel):
    node: Node
    edge: Edge


class DiscoveryCandidateRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=_MAX_DISCOVERY_TEXT_LENGTH)
    title: str = Field(min_length=1, max_length=_MAX_DISCOVERY_TITLE_LENGTH)
    kind: ResourceKind | None = None
    description: str = Field(default="", max_length=_MAX_DISCOVERY_TEXT_LENGTH)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class DiscoveryPreviewRequest(BaseModel):
    workspace_id: str
    instruction: str = Field(min_length=1, max_length=_MAX_DISCOVERY_TEXT_LENGTH)
    sources_searched: list[str] = Field(default_factory=list, max_length=20)
    filters_interpreted: dict[str, object] = Field(default_factory=dict)
    candidates: list[DiscoveryCandidateRequest] = Field(
        min_length=1, max_length=_MAX_DISCOVERY_CANDIDATES
    )


class DiscoveryApplyRequest(DiscoveryPreviewRequest):
    agent_identity: str = Field(min_length=1, max_length=_MAX_DISCOVERY_TITLE_LENGTH)
