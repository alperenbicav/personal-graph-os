"""Request DTOs for the HTTP API.

Domain models (`Node`, `Edge`, `Canvas`, `CanvasPlacement`, `Workspace`) are already typed
Pydantic models and are returned directly as response bodies. Requests need their own shape
because a capture/update/move is a partial, caller-supplied input, not a full entity.

`ResourceResponse` is the one response DTO in this module: a `Resource` alone would omit its
backing `Node`'s title/body, and product decision #2 (`WORK.md`) forbids giving `Resource` its
own separate copy of them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from pydantic import BaseModel, Field, StrictInt

from personal_graph_os.application.activity_recording import undo_disabled_reason
from personal_graph_os.application.activity_service import ActivityEventPage
from personal_graph_os.application.capture_service import CaptureOutcome
from personal_graph_os.application.document_service import DocumentDetail
from personal_graph_os.application.enrichment_service import EnrichmentOutcome
from personal_graph_os.application.projections import ProjectionItem
from personal_graph_os.application.resource_detail_service import (
    RelatedDocument,
    RelatedNode,
    ResourceDetail,
)
from personal_graph_os.application.work_planning_service import WorkPlanOutcome
from personal_graph_os.application.workflow_chain import WorkflowChainStep
from personal_graph_os.domain.activity import ActivityEvent
from personal_graph_os.domain.agents import (
    Agent,
    AgentRun,
    AgentWriteMode,
)
from personal_graph_os.domain.capture import CaptureIntent, CapturePayloadKind
from personal_graph_os.domain.documents import (
    Collection,
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
    DocumentVersion,
    Tag,
)
from personal_graph_os.domain.enrichment import (
    RelationProposal,
    ResourceEnrichmentProfile,
    ResourceEnrichmentProfileVersion,
)
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import ActivityEventId
from personal_graph_os.domain.ingestion import IngestionJob
from personal_graph_os.domain.resource import (
    RepositoryLabel,
    Resource,
    ResourceKind,
    ResourceLifecycleStatus,
)
from personal_graph_os.domain.resource_content import ResourceContent
from personal_graph_os.domain.schema import FieldType
from personal_graph_os.domain.search import SearchResult
from personal_graph_os.domain.views import FilterField, ProjectionQuery, ViewKind
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemChecklistItem,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)

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
    repository_label: RepositoryLabel | None = None
    clear_repository_label: bool = False


class SearchResponse(BaseModel):
    """Scoped search page (ST-12): the caller-visible results plus the stable pagination contract
    the engine computed -- `total`/`has_more` describe the deduplicated, archived-filtered entity
    set, so a UI can offer a correct "load more" from `offset`."""

    results: list[SearchResult]
    total: int
    has_more: bool
    offset: int


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
    repository_label: RepositoryLabel | None
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
            repository_label=resource.repository_label,
            title=node.title,
            body=node.body,
        )


class RelatedNodeResponse(BaseModel):
    edge_id: str
    direction: str
    edge_type_name: str
    node_id: str
    node_title: str
    target_domain: str
    target_entity_id: str | None
    resource_kind: str | None

    @classmethod
    def from_related_node(cls, related: RelatedNode) -> RelatedNodeResponse:
        return cls(
            edge_id=related.edge_id,
            direction=related.direction,
            edge_type_name=related.edge_type_name,
            node_id=related.node_id,
            target_domain=related.target_domain,
            target_entity_id=related.target_entity_id,
            node_title=related.node_title,
            resource_kind=related.resource_kind,
        )


class RelatedDocumentResponse(BaseModel):
    document_id: str
    title: str
    kind: str

    @classmethod
    def from_related_document(cls, related: RelatedDocument) -> RelatedDocumentResponse:
        return cls(document_id=related.document_id, title=related.title, kind=related.kind)


class ProvenanceResponse(BaseModel):
    ingestion_job_id: str
    source: str
    stage: str
    status: str
    created_at: datetime

    @classmethod
    def from_ingestion_job(cls, job: IngestionJob) -> ProvenanceResponse:
        return cls(
            ingestion_job_id=job.id,
            source=job.source,
            stage=job.stage.value,
            status=job.status.value,
            created_at=job.created_at,
        )


class ResourceDetailResponse(BaseModel):
    resource: ResourceResponse
    enrichment: ResourceEnrichmentProfileVersion | None
    relations: list[RelatedNodeResponse]
    related_documents: list[RelatedDocumentResponse]
    provenance: ProvenanceResponse | None

    @classmethod
    def from_detail(cls, detail: ResourceDetail, node: Node) -> ResourceDetailResponse:
        return cls(
            resource=ResourceResponse.from_resource_and_node(detail.resource, node),
            enrichment=detail.enrichment,
            relations=[RelatedNodeResponse.from_related_node(r) for r in detail.relations],
            related_documents=[
                RelatedDocumentResponse.from_related_document(d) for d in detail.related_documents
            ],
            provenance=(
                ProvenanceResponse.from_ingestion_job(detail.provenance)
                if detail.provenance is not None
                else None
            ),
        )


class ResourceContentResponse(BaseModel):
    """A resource's persisted source content (EP-2026-012 ST-13): the bounded body text plus
    the provenance of the fetch that read it."""

    resource_id: str
    body_markdown: str
    content_hash: str
    adapter_name: str
    retrieved_at: datetime

    @classmethod
    def from_content(cls, content: ResourceContent) -> ResourceContentResponse:
        return cls(
            resource_id=content.resource_id,
            body_markdown=content.body_markdown,
            content_hash=content.content_hash,
            adapter_name=content.adapter_name,
            retrieved_at=content.retrieved_at,
        )


class EnrichResourceRequest(BaseModel):
    actor: str


class EnrichmentOutcomeResponse(BaseModel):
    profile: ResourceEnrichmentProfile
    version: ResourceEnrichmentProfileVersion
    relation_proposals: list[RelationProposal]

    @classmethod
    def from_outcome(cls, outcome: EnrichmentOutcome) -> EnrichmentOutcomeResponse:
        return cls(
            profile=outcome.profile,
            version=outcome.version,
            relation_proposals=list(outcome.relation_proposals),
        )


class CaptureRequest(BaseModel):
    workspace_id: str
    source: str
    request_id: str
    actor_name: str
    payload_kind: CapturePayloadKind
    url: str | None = None
    file_reference_id: str | None = None
    external_item_id: str | None = None
    text: str | None = None
    intent: CaptureIntent = CaptureIntent.SAVE_RAW
    title: str | None = None
    # Caller-supplied, already-resolved repository association (review finding S5-R02): the
    # classifier never proposes or resolves this itself, so it is only ever accepted here as an
    # explicit, typed reference, validated against the workspace before any provider call.
    repository_node_id: str | None = None


class CaptureOutcomeResponse(BaseModel):
    ingestion_job_id: str
    resource_id: str | None
    document_id: str | None
    pending_operations: list[str]
    needs_clarification: bool
    clarification_reason: str | None
    was_replayed: bool

    @classmethod
    def from_outcome(cls, outcome: CaptureOutcome) -> CaptureOutcomeResponse:
        return cls(
            ingestion_job_id=outcome.job.id,
            resource_id=outcome.resource_id,
            document_id=outcome.document_id,
            pending_operations=[operation.kind.value for operation in outcome.pending_operations],
            needs_clarification=outcome.needs_clarification,
            clarification_reason=outcome.clarification_reason,
            was_replayed=outcome.was_replayed,
        )


class WorkPlanOutcomeResponse(BaseModel):
    epic: WorkItem
    stories: list[WorkItem]
    tasks: list[WorkItem]
    plan_document: Document
    plan_version: DocumentVersion

    @classmethod
    def from_outcome(cls, outcome: WorkPlanOutcome) -> WorkPlanOutcomeResponse:
        return cls(
            epic=outcome.epic,
            stories=list(outcome.stories),
            tasks=list(outcome.tasks),
            plan_document=outcome.plan_document,
            plan_version=outcome.plan_version,
        )


class CaptureAndPlanResponse(BaseModel):
    capture: CaptureOutcomeResponse
    plan: WorkPlanOutcomeResponse | None


class ClickUpImportRequest(BaseModel):
    """Import one user-selected ClickUp task (EP-2026-012 ST-10). `task_id` is the stable
    external id (`request_id == task.id`); `intent` selects raw (`save_raw`) or planned
    (`plan_work`) capture exactly like the MCP `pgos_capture` tool."""

    workspace_id: str
    task_id: str
    intent: CaptureIntent = CaptureIntent.SAVE_RAW
    actor_name: str
    repository_node_id: str | None = None


class WorkItemResponse(BaseModel):
    """A `WorkItem` plus its backing `Node`'s title/body, mirroring `ResourceResponse` (the
    work item never carries its own copy of them)."""

    id: str
    workspace_id: str
    node_id: str
    kind: WorkItemKind
    work_type: WorkItemType
    status: WorkItemStatus
    parent_id: str | None
    repository_node_id: str | None
    priority: WorkItemPriority | None
    due_date: date | None
    assignee: str | None
    blockers: str | None
    progress_percent: int | None
    source: str
    created_at: datetime
    updated_at: datetime
    title: str
    body: str

    @classmethod
    def from_work_item_and_node(cls, work_item: WorkItem, node: Node) -> WorkItemResponse:
        return cls(
            id=work_item.id,
            workspace_id=work_item.workspace_id,
            node_id=work_item.node_id,
            kind=work_item.kind,
            work_type=work_item.work_type,
            status=work_item.status,
            parent_id=work_item.parent_id,
            repository_node_id=work_item.repository_node_id,
            priority=work_item.priority,
            due_date=work_item.due_date,
            assignee=work_item.assignee,
            blockers=work_item.blockers,
            progress_percent=work_item.progress_percent,
            source=work_item.source,
            created_at=work_item.created_at,
            updated_at=work_item.updated_at,
            title=node.title,
            body=node.body,
        )


class WorkItemDetailResponse(BaseModel):
    """Everything the Tasks workspace's detail pane needs in one round trip (ST-08): the work
    item itself, its checklist, and the Wiki documents linked to its backing node."""

    work_item: WorkItemResponse
    checklist_items: list[WorkItemChecklistItem]
    linked_documents: list[DocumentLink]


class CreateWorkItemRequest(BaseModel):
    workspace_id: str
    kind: WorkItemKind
    work_type: WorkItemType
    title: str
    body: str = ""
    source: str
    status: WorkItemStatus = WorkItemStatus.BACKLOG
    parent_id: str | None = None
    repository_node_id: str | None = None


class UpdateWorkItemRequest(BaseModel):
    work_type: WorkItemType | None = None
    status: WorkItemStatus | None = None
    priority: WorkItemPriority | None = None
    due_date: date | None = None
    assignee: str | None = None
    blockers: str | None = None
    progress_percent: StrictInt | None = Field(default=None, ge=0, le=100)
    repository_node_id: str | None = None
    clear_priority: bool = False
    clear_due_date: bool = False
    clear_assignee: bool = False
    clear_blockers: bool = False
    clear_progress_percent: bool = False
    clear_repository_node_id: bool = False


class CreateChecklistItemRequest(BaseModel):
    label: str


class UpdateChecklistItemRequest(BaseModel):
    label: str | None = None
    is_completed: bool | None = None


class ReorderChecklistItemsRequest(BaseModel):
    ordered_ids: list[str]


class AttachWorkItemWikiLinkRequest(BaseModel):
    document_id: str


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


class UndoActivityEventRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ActivityEventSummary(BaseModel):
    """One list-row summary -- never the before/after snapshot (ST07-F06): a page of up to
    100 events, each potentially carrying two 256 KiB snapshots, would otherwise approach
    50 MiB. `GET /activity-events/{id}` remains the only way to fetch a snapshot."""

    id: str
    workspace_id: str
    actor_kind: str
    actor_name: str
    source: str
    entity_type: str
    entity_id: str
    action: str
    reason: str | None
    is_undoable: bool
    occurred_at: datetime
    reverses_event_id: str | None
    # Stable machine-readable code for why this event cannot be undone right now (ST07-F05
    # re-review) -- `None` while it still can be. See
    # `activity_recording.undo_disabled_reason` for the shared taxonomy REST/undo/UI all use.
    disabled_reason: str | None
    # Human-readable subject for feed rendering (EP-2026-013 ST-10 review finding): derived
    # from the recorded snapshot's title/name so the UI never shows a raw UUID.
    entity_title: str | None = None

    @classmethod
    def from_domain(
        cls, event: ActivityEvent, *, is_already_reversed: bool
    ) -> ActivityEventSummary:
        entity_title: str | None = None
        for state in (event.after_state, event.before_state):
            if not isinstance(state, dict):
                continue
            candidate = state.get("title") or state.get("name")
            if isinstance(candidate, str) and candidate.strip():
                entity_title = candidate.strip()
                break
        return cls(
            id=event.id,
            workspace_id=event.workspace_id,
            actor_kind=event.actor_kind.value,
            actor_name=event.actor_name,
            source=event.source,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action.value,
            reason=event.reason,
            is_undoable=event.is_undoable,
            occurred_at=event.occurred_at,
            reverses_event_id=event.reverses_event_id,
            disabled_reason=undo_disabled_reason(event, is_already_reversed=is_already_reversed),
            entity_title=entity_title,
        )


class ActivityEventDetail(ActivityEventSummary):
    """Full detail, including bounded before/after snapshots (ST07-F06)."""

    before_state: dict[str, object] | None
    after_state: dict[str, object] | None
    request_id: str | None

    @classmethod
    def from_domain(cls, event: ActivityEvent, *, is_already_reversed: bool) -> ActivityEventDetail:
        summary = ActivityEventSummary.from_domain(event, is_already_reversed=is_already_reversed)
        return cls(
            **summary.model_dump(),
            before_state=event.before_state,
            after_state=event.after_state,
            request_id=event.request_id,
        )


class CreateDocumentRequest(BaseModel):
    workspace_id: str
    title: str
    kind: DocumentKind
    source: str
    body_markdown: str = ""
    collection_id: str | None = None
    tag_names: list[str] = Field(default_factory=list)


class UpdateDocumentMetadataRequest(BaseModel):
    title: str | None = None
    kind: DocumentKind | None = None
    collection_id: str | None = None
    clear_collection: bool = False
    tag_names: list[str] | None = None
    is_archived: bool | None = None


class EditDocumentBodyRequest(BaseModel):
    body_markdown: str
    actor: str


class CreateCollectionRequest(BaseModel):
    workspace_id: str
    name: str
    parent_id: str | None = None


class CreateTagRequest(BaseModel):
    workspace_id: str
    name: str


class AddDocumentLinkRequest(BaseModel):
    target_type: DocumentLinkTargetType
    target_id: str


class DocumentDetailResponse(BaseModel):
    document: Document
    latest_version: DocumentVersion
    collection: Collection | None
    tags: list[Tag]
    outbound_links: list[DocumentLink]
    backlinks: list[Document]
    version_count: int

    @classmethod
    def from_detail(cls, detail: DocumentDetail) -> DocumentDetailResponse:
        return cls(
            document=detail.document,
            latest_version=detail.latest_version,
            collection=detail.collection,
            tags=list(detail.tags),
            outbound_links=list(detail.outbound_links),
            backlinks=list(detail.backlinks),
            version_count=detail.version_count,
        )


class ActivityEventPageResponse(BaseModel):
    events: list[ActivityEventSummary]
    next_cursor: str | None

    @classmethod
    def from_page(
        cls, page: ActivityEventPage, *, is_already_reversed: Callable[[ActivityEventId], bool]
    ) -> ActivityEventPageResponse:
        return cls(
            events=[
                ActivityEventSummary.from_domain(
                    event, is_already_reversed=is_already_reversed(event.id)
                )
                for event in page.events
            ],
            next_cursor=page.next_cursor,
        )


class CreateAgentRequest(BaseModel):
    name: str
    system_prompt: str
    emoji: str = "🤖"
    tool_allowlist: list[str] = Field(default_factory=list)
    model: str | None = None
    write_mode: AgentWriteMode = AgentWriteMode.PROPOSAL


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    emoji: str | None = None
    tool_allowlist: list[str] | None = None
    model: str | None = None
    write_mode: AgentWriteMode | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    emoji: str
    system_prompt: str
    tool_allowlist: list[str]
    model: str | None
    write_mode: str
    created_at: datetime

    @classmethod
    def from_domain(cls, agent: Agent) -> AgentResponse:
        return cls(
            id=str(agent.id),
            name=agent.name,
            emoji=agent.emoji,
            system_prompt=agent.system_prompt,
            tool_allowlist=agent.tool_allowlist,
            model=agent.model,
            write_mode=agent.write_mode.value,
            created_at=agent.created_at,
        )


class AgentRunResponse(BaseModel):
    id: str
    agent_id: str
    action: str
    entity_type: str | None
    entity_id: str | None
    status: str
    summary: str
    diff_json: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, run: AgentRun) -> AgentRunResponse:
        return cls(
            id=str(run.id),
            agent_id=str(run.agent_id),
            action=run.action,
            entity_type=run.entity_type,
            entity_id=run.entity_id,
            status=run.status.value,
            summary=run.summary,
            diff_json=run.diff_json,
            created_at=run.created_at,
        )


class AgentMessageRequest(BaseModel):
    content: str


class AgentMessageResponse(BaseModel):
    reply: str
    run_id: str

