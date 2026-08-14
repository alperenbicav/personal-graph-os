"""`AgentGatewayService`: the one application-layer dependency MCP tools call.

No handler in `infrastructure/mcp/server.py` talks to a repository, application service, or
REST route directly; every read and mutation goes through this service so REST and MCP stay
behaviorally identical and the SDK adapter never needs application/domain knowledge beyond
this narrow surface. Mutations reuse the `*_within` service paths and `ResearchUnitOfWork` so
canonical validation and atomic multi-table commit never fork from REST's.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from personal_graph_os.application.activity_recording import MutationContext, record_activity_event
from personal_graph_os.application.activity_service import (
    ActivityEventNotFoundError,
    ActivityService,
)
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.clickup_service import ClickupService
from personal_graph_os.application.context_pack_service import (
    ContextPackNotFoundError,
    ContextPackSelectionError,
    ContextPackService,
    ContextPackTokenBudgetError,
)
from personal_graph_os.application.discovery import DiscoveryCandidateInput, DiscoveryService
from personal_graph_os.application.document_service import DocumentNotFoundError, DocumentService
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.extraction_service import ExtractionService
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
from personal_graph_os.application.work_item_service import (
    WorkItemNotFoundError,
    WorkItemService,
    WorkItemUpdatePatch,
)
from personal_graph_os.application.workflow_chain import WorkflowChainService, WorkflowChainStep
from personal_graph_os.domain.activity import IdempotencyReceipt, MutationAction
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.enrichment import RelationProposalStatus
from personal_graph_os.domain.identifiers import (
    ActivityEventId,
    CollectionId,
    ContextPackId,
    DocumentId,
    EdgeId,
    EdgeTypeId,
    IngestionJobId,
    NodeId,
    NodeTypeId,
    RelationProposalId,
    ResourceId,
    StatusDefinitionId,
    WorkItemChecklistItemId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import ResourceKind, ResourceLifecycleStatus
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)
from personal_graph_os.infrastructure.mcp.dto import (
    ActivityEventDTO,
    ActivityEventPageDTO,
    ActivityEventSummaryDTO,
    CollectionDTO,
    ContextPackDTO,
    DiscoveryPreviewDTO,
    DiscoveryRunDTO,
    DocumentDTO,
    DocumentVersionDTO,
    EdgeDTO,
    EvidencePointerDTO,
    IngestionJobDTO,
    MaterializedContextPackDTO,
    NodeDTO,
    RelationProposalDTO,
    ResourceDTO,
    SearchHitDTO,
    TagDTO,
    WorkItemChecklistItemDTO,
    WorkItemDTO,
    WorkspaceDTO,
)

MAX_LIST_LIMIT = 100
MAX_SEARCH_LIMIT = 50
MAX_ACTOR_NAME_LENGTH = 200
MAX_REASON_LENGTH = 1_000
MAX_REQUEST_ID_LENGTH = 200
MAX_TITLE_LENGTH = 300
MAX_IMPORT_TEXT_LENGTH = 4_000
MAX_IMPORT_CANDIDATES = 50
MAX_IMPORT_EVIDENCE_POINTERS = 20
MAX_CONTEXT_PACK_OBJECTS = 200
MAX_CONTEXT_PACK_TOKEN_LIMIT = 32_000
# A serialized-field/text boundary (decision-adjacent, `WORK.md` Input maxima): bounds a
# node's `body` and the total serialized size of `field_values`/`filters_interpreted`, so an
# opaque, unbounded-shape input cannot grow a stored row without limit (ST06-F04).
MAX_SERIALIZED_FIELD_BYTES = 64 * 1024
# Context Pack evidence pointers use the plan's dedicated bound (20), never the unrelated
# `MAX_CONTEXT_PACK_OBJECTS` (200) node/edge bound (ST06-F04).
MAX_EVIDENCE_POINTERS = 20
MAX_EVIDENCE_POINTER_LENGTH = 500
MAX_SOURCES_SEARCHED = 50
MAX_RESOURCE_TEXT_LIST_ITEMS = 50
MAX_INCLUSION_REASON_LENGTH = 1_000
MAX_DOCUMENT_TAG_NAMES = 20
MAX_DOCUMENT_TAG_NAME_LENGTH = 100

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


def _validate_bounded_bytes(value: str, *, field_name: str, maximum_bytes: int) -> str:
    """Bound a text field by its serialized (UTF-8) byte size rather than character count
    (ST06-F04): `body`/serialized field values are opaque, potentially multi-byte content."""
    if len(value.encode("utf-8")) > maximum_bytes:
        raise GatewayValidationError(f"{field_name} must not exceed {maximum_bytes} bytes")
    return value


def _validate_serialized_bytes(value: object, *, field_name: str, maximum_bytes: int) -> None:
    serialized = json.dumps(value, default=str)
    if len(serialized.encode("utf-8")) > maximum_bytes:
        raise GatewayValidationError(f"{field_name} must not exceed {maximum_bytes} bytes")


def _validate_bounded_text_sequence(
    values: Sequence[str], *, field_name: str, max_items: int, max_item_length: int
) -> tuple[str, ...]:
    if len(values) > max_items:
        raise GatewayValidationError(f"{field_name} must not exceed {max_items} entries")
    return tuple(
        _validate_bounded_text(value, field_name=field_name, maximum=max_item_length)
        for value in values
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    """A stable content hash of one operation's canonicalized arguments (ST06-F01): binds an
    idempotency receipt to the exact tool call that produced it, so a reused `request_id` with
    a different tool or a changed payload is rejected as a conflict instead of replayed."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        discovery_service: DiscoveryService,
        context_pack_service: ContextPackService,
        activity_service: ActivityService,
        document_service: DocumentService,
        work_item_service: WorkItemService,
        enrichment_service: EnrichmentService | None,
        extraction_service: ExtractionService | None,
        capture_planning_orchestrator: CapturePlanningOrchestrator | None,
        clickup_service: ClickupService | None = None,
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
        self._discovery_service = discovery_service
        self._context_pack_service = context_pack_service
        self._activity_service = activity_service
        self._document_service = document_service
        self._work_item_service = work_item_service
        self._enrichment_service = enrichment_service
        self._extraction_service = extraction_service
        self._capture_planning_orchestrator = capture_planning_orchestrator
        self._clickup_service = clickup_service
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
        # Newest first (ST06-F07), with a deterministic id tie-breaker for equal timestamps.
        ordered = sorted(nodes, key=lambda node: (node.created_at, node.id), reverse=True)
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
        scope: str = "all",
        limit: int = MAX_SEARCH_LIMIT,
    ) -> dict[str, object]:
        _validate_limit(limit, maximum=MAX_SEARCH_LIMIT)
        self._require_workspace(workspace_id)
        from personal_graph_os.domain.search import SearchScope

        page = self._search.search(
            workspace_id,
            query_text,
            scope=SearchScope(scope),
            limit=limit,
            include_archived=include_archived,
        )
        return {
            "hits": [
                SearchHitDTO.from_domain(result).model_dump(mode="json") for result in page.results
            ],
            "total": page.total,
            "has_more": page.has_more,
            "offset": page.offset,
        }

    def list_resources(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int = MAX_LIST_LIMIT,
    ) -> tuple[ResourceDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        resources = self._resources.list_by_workspace(workspace_id)
        # Most recently active first (ST06-F07), with a deterministic id tie-breaker.
        ordered = sorted(
            resources, key=lambda resource: (resource.last_activity_at, resource.id), reverse=True
        )
        return tuple(ResourceDTO.from_domain(resource) for resource in ordered[:limit])

    def list_activity_events(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int = MAX_LIST_LIMIT,
        cursor: str | None = None,
    ) -> ActivityEventPageDTO:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        try:
            page = self._activity_service.list_workspace_events(
                workspace_id, limit=limit, cursor=cursor
            )
        except ValueError as error:
            raise GatewayValidationError(str(error)) from error
        return ActivityEventPageDTO(
            events=tuple(ActivityEventSummaryDTO.from_domain(event) for event in page.events),
            next_cursor=page.next_cursor,
        )

    def get_activity_event(
        self, workspace_id: WorkspaceId, event_id: ActivityEventId
    ) -> ActivityEventDTO:
        try:
            event = self._activity_service.get_event(workspace_id, event_id)
        except ActivityEventNotFoundError as error:
            raise GatewayNotFoundError(str(error)) from error
        return ActivityEventDTO.from_domain(event)

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

    def _lookup_receipt(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        actor_name: str,
        request_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        """Look up an existing idempotency receipt for this key (ST06-F01).

        Returns the payload fingerprint (always, so a fresh call can save it after mutating)
        and the previously stored result, or `None` when this is the first call with this key.
        A receipt whose `operation` or `payload_fingerprint` does not match this call is a key
        reused for a different tool or a changed payload — a bounded conflict, never a replay.
        """
        fingerprint = _fingerprint(payload)
        receipt = unit_of_work.idempotency_receipts.get_by_request(
            workspace_id, _MCP_SOURCE, actor_name, request_id
        )
        if receipt is None:
            return fingerprint, None
        if receipt.operation != operation or receipt.payload_fingerprint != fingerprint:
            raise GatewayConflictError(
                f"request_id {request_id!r} was already used by actor {actor_name!r} for a "
                "different operation or a different payload"
            )
        return fingerprint, receipt.result_payload

    def _save_receipt(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        actor_name: str,
        request_id: str,
        operation: str,
        fingerprint: str,
        result_payload: dict[str, Any],
    ) -> None:
        receipt = IdempotencyReceipt(
            workspace_id=workspace_id,
            source=_MCP_SOURCE,
            actor_name=actor_name,
            request_id=request_id,
            operation=operation,
            payload_fingerprint=fingerprint,
            result_payload=result_payload,
        )
        try:
            unit_of_work.idempotency_receipts.save_without_commit(receipt)
        except sqlite3.IntegrityError as error:
            raise GatewayConflictError(
                f"request_id {request_id!r} was already claimed by actor {actor_name!r} by a "
                "concurrent request"
            ) from error

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
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> None:
        """Route every MCP mutation through the same bounded attribution/snapshot/
        undoability contract REST uses (ST07-F03): a bare event with no snapshot could never
        be explained by Activity detail nor reversed by human REST undo."""
        context = MutationContext.mcp(actor_name=actor_name, reason=reason, request_id=request_id)
        try:
            record_activity_event(
                unit_of_work,
                workspace_id=workspace_id,
                context=context,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                before_state=before_state,
                after_state=after_state,
            )
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
        payload = {"node_type_id": str(node_type_id), "title": title, "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_node",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                after_state=node.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "node": NodeDTO.from_domain(node).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_node",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        self._node_service.index_captured_node(node)
        return {**result_payload, "replayed": False}

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
        if body is not None:
            body = _validate_bounded_bytes(
                body, field_name="body", maximum_bytes=MAX_SERIALIZED_FIELD_BYTES
            )
        if field_values is not None:
            _validate_serialized_bytes(
                field_values, field_name="field_values", maximum_bytes=MAX_SERIALIZED_FIELD_BYTES
            )
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {
            "node_id": str(node_id),
            "title": title,
            "body": body,
            "status_id": str(status_id) if status_id is not None else None,
            "field_values": field_values,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_node",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                before_state=existing.model_dump(mode="json"),
                after_state=node.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "node": NodeDTO.from_domain(node).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_node",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        self._node_service.index_captured_node(node)
        return {**result_payload, "replayed": False}

    def archive_node(
        self, node_id: NodeId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"Node {node_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"node_id": str(node_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_node",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                before_state=existing.model_dump(mode="json"),
                after_state=node.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "node": NodeDTO.from_domain(node).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_node",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

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
        payload = {
            "edge_type_id": str(edge_type_id),
            "source_node_id": str(source_node_id),
            "target_node_id": str(target_node_id),
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="connect_nodes",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                after_state=edge.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "edge": EdgeDTO.from_domain(edge).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="connect_nodes",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

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
        raw_source = _validate_bounded_text(
            raw_source, field_name="raw_source", maximum=MAX_IMPORT_TEXT_LENGTH
        )
        body = _validate_bounded_bytes(
            body, field_name="body", maximum_bytes=MAX_SERIALIZED_FIELD_BYTES
        )
        payload = {
            "title": title,
            "raw_source": raw_source,
            "kind": kind.value if kind is not None else None,
            "body": body,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_or_reuse_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                    after_state=resource.model_dump(mode="json"),
                )
            result_payload: dict[str, Any] = {
                "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
                "was_created": was_created,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_or_reuse_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        if was_created and node is not None:
            self._resource_service.index_created_resource(resource, node)
        return {**result_payload, "replayed": False}

    def upsert_document(
        self,
        workspace_id: WorkspaceId,
        title: str,
        body_markdown: str,
        *,
        kind: DocumentKind,
        document_id: DocumentId | None,
        collection_id: CollectionId | None,
        tag_names: Sequence[str],
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Create a new Wiki document or, when `document_id` is given, or an exact title match
        already exists, append a new version to it instead (ST-07 agent upsert)."""
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        body_markdown = _validate_bounded_bytes(
            body_markdown, field_name="body_markdown", maximum_bytes=MAX_SERIALIZED_FIELD_BYTES
        )
        tag_names = _validate_bounded_text_sequence(
            tag_names,
            field_name="tag_names",
            max_items=MAX_DOCUMENT_TAG_NAMES,
            max_item_length=MAX_DOCUMENT_TAG_NAME_LENGTH,
        )
        self._require_workspace(workspace_id)
        payload = {
            "title": title,
            "body_markdown": body_markdown,
            "kind": kind.value,
            "document_id": document_id,
            "collection_id": collection_id,
            "tag_names": tag_names,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="upsert_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            document, version, was_created = self._document_service.upsert_for_agent_within(
                unit_of_work,
                workspace_id,
                title=title,
                body_markdown=body_markdown,
                actor=actor_name,
                kind=kind,
                document_id=document_id,
                collection_id=collection_id,
                tag_names=tag_names,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="document",
                entity_id=document.id,
                action=MutationAction.CREATED if was_created else MutationAction.UPDATED,
                after_state=document.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "document": DocumentDTO.from_domain(document).model_dump(mode="json"),
                "version": DocumentVersionDTO.from_domain(version).model_dump(mode="json"),
                "was_created": was_created,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="upsert_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        # Index after the unit of work commits (ST-12): `index_document` owns its own connection
        # transaction and would end the still-open caller transaction if called inside.
        self._document_service.index_document_for_search(document.id)
        return {**result_payload, "replayed": False}

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
        if next_action is not None:
            next_action = _validate_bounded_text(
                next_action, field_name="next_action", maximum=MAX_TITLE_LENGTH
            )
        if open_questions is not None:
            open_questions = _validate_bounded_text_sequence(
                open_questions,
                field_name="open_questions",
                max_items=MAX_RESOURCE_TEXT_LIST_ITEMS,
                max_item_length=MAX_IMPORT_TEXT_LENGTH,
            )
        if takeaways is not None:
            takeaways = _validate_bounded_text_sequence(
                takeaways,
                field_name="takeaways",
                max_items=MAX_RESOURCE_TEXT_LIST_ITEMS,
                max_item_length=MAX_IMPORT_TEXT_LENGTH,
            )
        payload = {
            "resource_id": str(resource_id),
            "lifecycle_status": lifecycle_status.value if lifecycle_status is not None else None,
            "next_action": next_action,
            "clear_next_action": clear_next_action,
            "next_action_dismissed": next_action_dismissed,
            "open_questions": list(open_questions) if open_questions is not None else None,
            "takeaways": list(takeaways) if takeaways is not None else None,
            "progress_percent": progress_percent,
            "clear_progress_percent": clear_progress_percent,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.resources.get(resource_id)
            if existing is None:
                raise GatewayNotFoundError(f"Resource {resource_id} does not exist")
            workspace_id = existing.workspace_id
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                    before_state=existing.model_dump(mode="json"),
                    after_state=resource.model_dump(mode="json"),
                )
            result_payload: dict[str, Any] = {
                "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
                "modified": was_modified,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        if was_modified:
            self._resource_service.index_updated_resource(resource)
        return {**result_payload, "replayed": False}

    def archive_resource(
        self, resource_id: ResourceId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        payload = {"resource_id": str(resource_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.resources.get(resource_id)
            if existing is None:
                raise GatewayNotFoundError(f"Resource {resource_id} does not exist")
            workspace_id = existing.workspace_id
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
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
                    before_state=existing.model_dump(mode="json"),
                    after_state=resource.model_dump(mode="json"),
                )
            result_payload: dict[str, Any] = {
                "resource": ResourceDTO.from_domain(resource).model_dump(mode="json"),
                "modified": was_modified,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        if was_modified:
            self._resource_service.index_updated_resource(resource)
        return {**result_payload, "replayed": False}

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
        payload = {
            "source_node_id": str(source_node_id),
            "step": step.value,
            "title": title,
            "existing_target_node_id": (
                str(existing_target_node_id) if existing_target_node_id is not None else None
            ),
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="advance_workflow",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            target_node, edge = self._workflow_chain_service.advance_within(
                unit_of_work,
                workspace_id,
                source_node_id,
                step,
                title=title,
                existing_target_node_id=existing_target_node_id,
            )
            # Matches REST's `WorkflowChainService.advance` exactly: one logical event for
            # this compound (node + edge) write, entity_type "workflow_chain_step" so it
            # never falls into the plain-edge undo allowlist -- undoing only the edge would
            # leave a freshly created target node orphaned (ST07-F03).
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="workflow_chain_step",
                entity_id=edge.id,
                action=MutationAction.CREATED,
                after_state={
                    "step": step.value,
                    "target_node": target_node.model_dump(mode="json"),
                    "edge": edge.model_dump(mode="json"),
                },
            )
            result_payload: dict[str, Any] = {
                "target_node": NodeDTO.from_domain(target_node).model_dump(mode="json"),
                "edge": EdgeDTO.from_domain(edge).model_dump(mode="json"),
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="advance_workflow",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    # -- Provenance-preserving import (06.3, decisions #4/#14, `WORK.md`) -------------------

    def _validate_import_candidates(
        self, candidates: Sequence[DiscoveryCandidateInput]
    ) -> tuple[DiscoveryCandidateInput, ...]:
        if not candidates:
            raise GatewayValidationError("candidates must not be empty")
        if len(candidates) > MAX_IMPORT_CANDIDATES:
            raise GatewayValidationError(
                f"candidates must not exceed {MAX_IMPORT_CANDIDATES}, got {len(candidates)}"
            )
        validated: list[DiscoveryCandidateInput] = []
        for candidate in candidates:
            identifier = _validate_bounded_text(
                candidate.identifier, field_name="identifier", maximum=MAX_IMPORT_TEXT_LENGTH
            )
            title = _validate_bounded_text(
                candidate.title, field_name="title", maximum=MAX_TITLE_LENGTH
            )
            if len(candidate.description) > MAX_IMPORT_TEXT_LENGTH:
                raise GatewayValidationError(
                    f"description must not exceed {MAX_IMPORT_TEXT_LENGTH} characters"
                )
            if len(candidate.evidence) > MAX_IMPORT_EVIDENCE_POINTERS:
                raise GatewayValidationError(
                    f"evidence must not exceed {MAX_IMPORT_EVIDENCE_POINTERS} entries"
                )
            evidence = tuple(
                _validate_bounded_text(item, field_name="evidence", maximum=MAX_IMPORT_TEXT_LENGTH)
                for item in candidate.evidence
            )
            validated.append(
                candidate.model_copy(
                    update={"identifier": identifier, "title": title, "evidence": evidence}
                )
            )
        return tuple(validated)

    def _validate_sources_and_filters(
        self,
        sources_searched: Sequence[str],
        filters_interpreted: dict[str, object] | None,
    ) -> tuple[tuple[str, ...], dict[str, object] | None]:
        validated_sources = _validate_bounded_text_sequence(
            sources_searched,
            field_name="sources_searched",
            max_items=MAX_SOURCES_SEARCHED,
            max_item_length=MAX_IMPORT_TEXT_LENGTH,
        )
        if filters_interpreted is not None:
            _validate_serialized_bytes(
                filters_interpreted,
                field_name="filters_interpreted",
                maximum_bytes=MAX_SERIALIZED_FIELD_BYTES,
            )
        return validated_sources, filters_interpreted

    def preview_import(
        self,
        workspace_id: WorkspaceId,
        instruction: str,
        candidates: Sequence[DiscoveryCandidateInput],
        *,
        sources_searched: Sequence[str] = (),
        filters_interpreted: dict[str, object] | None = None,
    ) -> DiscoveryPreviewDTO:
        instruction = _validate_bounded_text(
            instruction, field_name="instruction", maximum=MAX_IMPORT_TEXT_LENGTH
        )
        validated_candidates = self._validate_import_candidates(candidates)
        sources_searched, filters_interpreted = self._validate_sources_and_filters(
            sources_searched, filters_interpreted
        )
        preview = self._discovery_service.preview(
            workspace_id,
            instruction,
            validated_candidates,
            sources_searched=sources_searched,
            filters_interpreted=filters_interpreted,
        )
        return DiscoveryPreviewDTO.from_domain(preview)

    def apply_import(
        self,
        workspace_id: WorkspaceId,
        instruction: str,
        candidates: Sequence[DiscoveryCandidateInput],
        *,
        sources_searched: Sequence[str] = (),
        filters_interpreted: dict[str, object] | None = None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        instruction = _validate_bounded_text(
            instruction, field_name="instruction", maximum=MAX_IMPORT_TEXT_LENGTH
        )
        validated_candidates = self._validate_import_candidates(candidates)
        sources_searched, filters_interpreted = self._validate_sources_and_filters(
            sources_searched, filters_interpreted
        )
        payload = {
            "instruction": instruction,
            "candidates": [c.model_dump(mode="json") for c in validated_candidates],
            "sources_searched": list(sources_searched),
            "filters_interpreted": filters_interpreted or {},
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="apply_import",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            run, newly_created = self._discovery_service.apply_within(
                unit_of_work,
                workspace_id,
                actor_name,
                instruction,
                validated_candidates,
                sources_searched=sources_searched,
                filters_interpreted=filters_interpreted,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="discovery_run",
                entity_id=run.id,
                action=MutationAction.CREATED,
            )
            result_payload: dict[str, Any] = {
                "run": DiscoveryRunDTO.from_domain(run).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="apply_import",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        for resource, node in newly_created:
            self._resource_service.index_created_resource(resource, node)
        return {**result_payload, "replayed": False}

    # -- Immutable Context Packs (06.4, decisions #15/#16/#17, `WORK.md`) -------------------

    def _validate_inclusion_reasons(
        self, identifiers: Sequence[str], inclusion_reasons: dict[str, str]
    ) -> dict[str, str]:
        validated: dict[str, str] = {}
        for identifier in identifiers:
            reason = inclusion_reasons.get(identifier)
            if reason is None:
                raise GatewayValidationError(f"missing inclusion reason for {identifier!r}")
            validated[identifier] = _validate_bounded_text(
                reason, field_name="inclusion_reasons", maximum=MAX_INCLUSION_REASON_LENGTH
            )
        return validated

    def create_context_pack(
        self,
        workspace_id: WorkspaceId,
        name: str,
        *,
        node_ids: Sequence[NodeId] = (),
        edge_ids: Sequence[EdgeId] = (),
        evidence_pointers: Sequence[str] = (),
        inclusion_reasons: dict[str, str],
        object_limit: int = MAX_CONTEXT_PACK_OBJECTS,
        token_limit: int | None = None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        name = _validate_bounded_text(name, field_name="name", maximum=MAX_TITLE_LENGTH)
        total_objects = len(set(node_ids) | set(edge_ids))
        if total_objects > MAX_CONTEXT_PACK_OBJECTS:
            raise GatewayValidationError(
                f"selected objects must not exceed {MAX_CONTEXT_PACK_OBJECTS}, got {total_objects}"
            )
        if object_limit < 1 or object_limit > MAX_CONTEXT_PACK_OBJECTS:
            raise GatewayValidationError(
                f"object_limit must be between 1 and {MAX_CONTEXT_PACK_OBJECTS}"
            )
        if token_limit is not None and (
            token_limit < 1 or token_limit > MAX_CONTEXT_PACK_TOKEN_LIMIT
        ):
            raise GatewayValidationError(
                f"token_limit must be between 1 and {MAX_CONTEXT_PACK_TOKEN_LIMIT}"
            )
        deduped_evidence_pointers = tuple(dict.fromkeys(evidence_pointers))
        evidence_pointers = _validate_bounded_text_sequence(
            deduped_evidence_pointers,
            field_name="evidence_pointers",
            max_items=MAX_EVIDENCE_POINTERS,
            max_item_length=MAX_EVIDENCE_POINTER_LENGTH,
        )
        validated_reasons = self._validate_inclusion_reasons(
            (*node_ids, *edge_ids, *evidence_pointers), inclusion_reasons
        )
        payload = {
            "name": name,
            "node_ids": [str(node_id) for node_id in node_ids],
            "edge_ids": [str(edge_id) for edge_id in edge_ids],
            "evidence_pointers": list(evidence_pointers),
            "inclusion_reasons": validated_reasons,
            "object_limit": object_limit,
            "token_limit": token_limit,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_context_pack",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            try:
                context_pack = self._context_pack_service.create_within(
                    unit_of_work,
                    workspace_id,
                    name,
                    node_ids=node_ids,
                    edge_ids=edge_ids,
                    evidence_pointers=evidence_pointers,
                    inclusion_reasons=validated_reasons,
                    object_limit=object_limit,
                    token_limit=token_limit,
                )
            except (ContextPackSelectionError, ContextPackTokenBudgetError) as error:
                raise GatewayValidationError(str(error)) from error
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="context_pack",
                entity_id=context_pack.id,
                action=MutationAction.CREATED,
                after_state=context_pack.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "context_pack": ContextPackDTO.from_domain(context_pack).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_context_pack",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def list_context_packs(self, workspace_id: WorkspaceId) -> tuple[ContextPackDTO, ...]:
        self._require_workspace(workspace_id)
        context_packs = self._context_pack_service.list_by_workspace(workspace_id)
        ordered = sorted(context_packs, key=lambda pack: (pack.created_at, pack.id))
        return tuple(ContextPackDTO.from_domain(pack) for pack in ordered)

    def get_context_pack(self, context_pack_id: ContextPackId) -> ContextPackDTO:
        try:
            context_pack = self._context_pack_service.get(context_pack_id)
        except ContextPackNotFoundError as error:
            raise GatewayNotFoundError(str(error)) from error
        return ContextPackDTO.from_domain(context_pack)

    def materialize_context_pack(
        self, context_pack_id: ContextPackId
    ) -> MaterializedContextPackDTO:
        try:
            materialization = self._context_pack_service.materialize(context_pack_id)
        except ContextPackNotFoundError as error:
            raise GatewayNotFoundError(str(error)) from error
        return MaterializedContextPackDTO.from_domain(materialization)

    def delete_context_pack(
        self,
        workspace_id: WorkspaceId,
        context_pack_id: ContextPackId,
        *,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        """`workspace_id` is required (unlike `archive_*`): a hard delete leaves nothing to
        read the owning workspace from on a replay, so the replay lookup must be keyed by the
        caller-supplied workspace_id checked *before* the pack is resolved or removed."""
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        payload = {"context_pack_id": str(context_pack_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_context_pack",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            existing = unit_of_work.context_packs.get(context_pack_id)
            if existing is None or existing.workspace_id != workspace_id:
                raise GatewayNotFoundError(f"Context pack {context_pack_id} does not exist")
            deleted = self._context_pack_service.delete_within(unit_of_work, context_pack_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="context_pack",
                entity_id=deleted.id,
                action=MutationAction.DELETED,
                before_state=deleted.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"context_pack_id": deleted.id}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_context_pack",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    # ------------------------------------------------------------------
    # ST-09: work items
    # ------------------------------------------------------------------

    def _work_item_dto(self, work_item: WorkItem) -> WorkItemDTO:
        node = self._nodes.get(work_item.node_id)
        if node is None:
            raise GatewayNotFoundError(
                f"work item {work_item.id}'s backing node {work_item.node_id} is missing"
            )
        return WorkItemDTO.from_work_item_and_node(work_item, node)

    def list_work_items(
        self, workspace_id: WorkspaceId, *, include_archived: bool = False
    ) -> tuple[WorkItemDTO, ...]:
        self._require_workspace(workspace_id)
        return tuple(
            self._work_item_dto(item)
            for item in self._work_item_service.list_workspace(
                workspace_id, include_archived=include_archived
            )
        )

    def get_work_item(self, work_item_id: WorkItemId) -> WorkItemDTO:
        try:
            return self._work_item_dto(self._work_item_service.get(work_item_id))
        except WorkItemNotFoundError as error:
            raise GatewayNotFoundError(str(error)) from error

    def create_work_item(
        self,
        workspace_id: WorkspaceId,
        *,
        kind: WorkItemKind,
        work_type: WorkItemType,
        title: str,
        body: str,
        status: WorkItemStatus,
        parent_id: WorkItemId | None,
        repository_node_id: NodeId | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        title = _validate_bounded_text(title, field_name="title", maximum=MAX_TITLE_LENGTH)
        body = _validate_bounded_bytes(
            body, field_name="body", maximum_bytes=MAX_SERIALIZED_FIELD_BYTES
        )
        payload = {
            "kind": kind.value,
            "work_type": work_type.value,
            "title": title,
            "status": status.value,
            "parent_id": str(parent_id) if parent_id is not None else None,
            "repository_node_id": str(repository_node_id)
            if repository_node_id is not None
            else None,
            "reason": reason,
        }
        workspace = self._work_item_service.require_workspace(workspace_id)
        node_type = self._work_item_service.require_node_type(workspace)
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_work_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            work_item, node = self._work_item_service.create_within(
                unit_of_work,
                workspace,
                node_type,
                kind=kind,
                work_type=work_type,
                title=title,
                body=body,
                source=f"agent:{actor_name}",
                status=status,
                parent_id=parent_id,
                repository_node_id=repository_node_id,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item.id,
                action=MutationAction.CREATED,
                after_state=work_item.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "work_item": self._work_item_dto(work_item).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="create_work_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        self._node_service.index_captured_node(node)
        return {**result_payload, "replayed": False}

    def update_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        work_type: WorkItemType | None,
        status: WorkItemStatus | None,
        priority: WorkItemPriority | None,
        due_date: date | None,
        assignee: str | None,
        blockers: str | None,
        progress_percent: int | None,
        repository_node_id: NodeId | None,
        clear_priority: bool,
        clear_due_date: bool,
        clear_assignee: bool,
        clear_blockers: bool,
        clear_progress_percent: bool,
        clear_repository_node_id: bool,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if assignee is not None:
            assignee = _validate_bounded_text(
                assignee, field_name="assignee", maximum=MAX_TITLE_LENGTH
            )
        if blockers is not None:
            blockers = _validate_bounded_text(
                blockers, field_name="blockers", maximum=MAX_REASON_LENGTH
            )
        patch = WorkItemUpdatePatch(
            work_type=work_type,
            status=status,
            priority=priority,
            due_date=due_date,
            assignee=assignee,
            blockers=blockers,
            progress_percent=progress_percent,
            repository_node_id=repository_node_id,
            clear_priority=clear_priority,
            clear_due_date=clear_due_date,
            clear_assignee=clear_assignee,
            clear_blockers=clear_blockers,
            clear_progress_percent=clear_progress_percent,
            clear_repository_node_id=clear_repository_node_id,
        )
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {
            "work_item_id": str(work_item_id),
            "work_type": work_type.value if work_type else None,
            "status": status.value if status else None,
            "priority": priority.value if priority else None,
            "due_date": due_date.isoformat() if due_date else None,
            "assignee": assignee,
            "blockers": blockers,
            "progress_percent": progress_percent,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_work_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._work_item_service.update_within(
                unit_of_work, workspace_id, work_item_id, patch
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"work_item": self._work_item_dto(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_work_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def reparent_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        parent_id: WorkItemId | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {
            "work_item_id": str(work_item_id),
            "parent_id": str(parent_id) if parent_id is not None else None,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reparent_work_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._work_item_service.reparent_within(
                unit_of_work, workspace_id, work_item_id, parent_id
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=updated.id,
                action=MutationAction.UPDATED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"work_item": self._work_item_dto(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reparent_work_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def _archive_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        is_archived: bool,
        operation: str,
        action: MutationAction,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {"work_item_id": str(work_item_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation=operation,
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            if is_archived:
                updated = self._work_item_service.archive_within(
                    unit_of_work, workspace_id, work_item_id
                )
            else:
                updated = self._work_item_service.restore_within(
                    unit_of_work, workspace_id, work_item_id
                )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=updated.id,
                action=action,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"work_item": self._work_item_dto(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation=operation,
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def archive_work_item(
        self, work_item_id: WorkItemId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        result = self._archive_work_item(
            work_item_id,
            is_archived=True,
            operation="archive_work_item",
            action=MutationAction.ARCHIVED,
            actor_name=actor_name,
            reason=reason,
            request_id=request_id,
        )
        return {**result, "recoverable": True}

    def restore_work_item(
        self, work_item_id: WorkItemId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        return self._archive_work_item(
            work_item_id,
            is_archived=False,
            operation="restore_work_item",
            action=MutationAction.RESTORED,
            actor_name=actor_name,
            reason=reason,
            request_id=request_id,
        )

    def delete_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        destructive: bool,
        confirm_id: str | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        self._require_destructive_confirmation(str(work_item_id), destructive, confirm_id)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {"work_item_id": str(work_item_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_work_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._work_item_service.delete_within(unit_of_work, workspace_id, work_item_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "work_item_id": work_item_id,
                "recoverable": False,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_work_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def add_checklist_item(
        self,
        work_item_id: WorkItemId,
        *,
        label: str,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        label = _validate_bounded_text(label, field_name="label", maximum=MAX_TITLE_LENGTH)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {"work_item_id": str(work_item_id), "label": label, "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="add_checklist_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            item = self._work_item_service.add_checklist_item_within(
                unit_of_work, workspace_id, work_item_id, label
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item_id,
                action=MutationAction.UPDATED,
                after_state=item.model_dump(mode="json"),
            )
            result_payload = {
                "checklist_item": WorkItemChecklistItemDTO.from_domain(item).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="add_checklist_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def update_checklist_item(
        self,
        checklist_item_id: WorkItemChecklistItemId,
        *,
        label: str | None,
        is_completed: bool | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if label is not None:
            label = _validate_bounded_text(label, field_name="label", maximum=MAX_TITLE_LENGTH)
        item = self._work_item_service.get_checklist_item(checklist_item_id)
        work_item = self._work_item_service.get(item.work_item_id)
        workspace_id = work_item.workspace_id
        payload = {
            "checklist_item_id": str(checklist_item_id),
            "label": label,
            "is_completed": is_completed,
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_checklist_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._work_item_service.update_checklist_item_within(
                unit_of_work,
                workspace_id,
                checklist_item_id,
                label=label,
                is_completed=is_completed,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item.id,
                action=MutationAction.UPDATED,
                before_state=item.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {
                "checklist_item": WorkItemChecklistItemDTO.from_domain(updated).model_dump(
                    mode="json"
                )
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="update_checklist_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def remove_checklist_item(
        self,
        checklist_item_id: WorkItemChecklistItemId,
        *,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        item = self._work_item_service.get_checklist_item(checklist_item_id)
        work_item = self._work_item_service.get(item.work_item_id)
        workspace_id = work_item.workspace_id
        payload = {"checklist_item_id": str(checklist_item_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="remove_checklist_item",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._work_item_service.remove_checklist_item_within(
                unit_of_work, workspace_id, checklist_item_id
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item.id,
                action=MutationAction.UPDATED,
                before_state=item.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"checklist_item_id": checklist_item_id}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="remove_checklist_item",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def reorder_checklist_items(
        self,
        work_item_id: WorkItemId,
        *,
        ordered_ids: tuple[WorkItemChecklistItemId, ...],
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {
            "work_item_id": str(work_item_id),
            "ordered_ids": [str(item_id) for item_id in ordered_ids],
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reorder_checklist_items",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            items = self._work_item_service.reorder_checklist_items_within(
                unit_of_work, workspace_id, work_item_id, ordered_ids
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item_id,
                action=MutationAction.UPDATED,
            )
            result_payload = {
                "checklist_items": [
                    WorkItemChecklistItemDTO.from_domain(item).model_dump(mode="json")
                    for item in items
                ]
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reorder_checklist_items",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def attach_document(
        self,
        work_item_id: WorkItemId,
        *,
        document_id: DocumentId,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {
            "work_item_id": str(work_item_id),
            "document_id": str(document_id),
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="attach_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            link = self._work_item_service.attach_document_within(
                unit_of_work,
                workspace_id=workspace_id,
                work_item_id=work_item_id,
                document_id=document_id,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item_id,
                action=MutationAction.UPDATED,
                after_state=link.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"document_link_id": link.id}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="attach_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def detach_document(
        self,
        work_item_id: WorkItemId,
        *,
        document_id: DocumentId,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._work_item_service.get(work_item_id)
        workspace_id = existing.workspace_id
        payload = {
            "work_item_id": str(work_item_id),
            "document_id": str(document_id),
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="detach_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._work_item_service.detach_document_within(
                unit_of_work,
                workspace_id=workspace_id,
                work_item_id=work_item_id,
                document_id=document_id,
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="work_item",
                entity_id=work_item_id,
                action=MutationAction.UPDATED,
            )
            result_payload: dict[str, Any] = {
                "work_item_id": work_item_id,
                "document_id": document_id,
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="detach_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    # ------------------------------------------------------------------
    # ST-09: document/resource/node/edge lifecycle
    # ------------------------------------------------------------------

    def list_documents(
        self, workspace_id: WorkspaceId, *, include_archived: bool = False
    ) -> tuple[DocumentDTO, ...]:
        self._require_workspace(workspace_id)
        return tuple(
            DocumentDTO.from_domain(document)
            for document in self._document_service.list_documents(
                workspace_id, include_archived=include_archived
            )
        )

    def get_document(self, document_id: DocumentId) -> DocumentDTO:
        try:
            return DocumentDTO.from_domain(self._document_service.get(document_id))
        except DocumentNotFoundError as error:
            raise GatewayNotFoundError(str(error)) from error

    def list_document_versions(self, document_id: DocumentId) -> tuple[DocumentVersionDTO, ...]:
        self._document_service.get(document_id)
        return tuple(
            DocumentVersionDTO.from_domain(version)
            for version in self._document_service.list_versions(document_id)
        )

    def get_document_version(
        self, document_id: DocumentId, *, version_number: int
    ) -> DocumentVersionDTO:
        versions = self._document_service.list_versions(document_id)
        for version in versions:
            if version.version_number == version_number:
                return DocumentVersionDTO.from_domain(version)
        raise GatewayNotFoundError(f"document {document_id} has no version {version_number}")

    def archive_document(
        self, document_id: DocumentId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._document_service.get(document_id)
        workspace_id = existing.workspace_id
        payload = {"document_id": str(document_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._document_service.update_metadata_within(
                unit_of_work, document_id, is_archived=True
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="document",
                entity_id=document_id,
                action=MutationAction.ARCHIVED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"document": DocumentDTO.from_domain(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="archive_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False, "recoverable": True}

    def restore_document(
        self, document_id: DocumentId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._document_service.get(document_id)
        workspace_id = existing.workspace_id
        payload = {"document_id": str(document_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._document_service.update_metadata_within(
                unit_of_work, document_id, is_archived=False
            )
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="document",
                entity_id=document_id,
                action=MutationAction.RESTORED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"document": DocumentDTO.from_domain(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def delete_document(
        self,
        document_id: DocumentId,
        *,
        destructive: bool,
        confirm_id: str | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        self._require_destructive_confirmation(str(document_id), destructive, confirm_id)
        existing = self._document_service.get(document_id)
        workspace_id = existing.workspace_id
        payload = {"document_id": str(document_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_document",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            unit_of_work.documents.delete_without_commit(document_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="document",
                entity_id=document_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"document_id": document_id, "recoverable": False}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_document",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def list_collections(self, workspace_id: WorkspaceId) -> tuple[CollectionDTO, ...]:
        self._require_workspace(workspace_id)
        return tuple(
            CollectionDTO.from_domain(collection)
            for collection in self._document_service.list_collections(workspace_id)
        )

    def list_tags(self, workspace_id: WorkspaceId) -> tuple[TagDTO, ...]:
        self._require_workspace(workspace_id)
        return tuple(
            TagDTO.from_domain(tag) for tag in self._document_service.list_tags(workspace_id)
        )

    def _require_destructive_confirmation(
        self, entity_id: str, destructive: bool, confirm_id: str | None
    ) -> None:
        if not destructive:
            raise GatewayValidationError("hard delete requires destructive: true")
        if confirm_id is None or confirm_id != entity_id:
            raise GatewayValidationError("hard delete requires confirm_id matching the entity id")

    def restore_resource(
        self, resource_id: ResourceId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._resources.get(resource_id)
        if existing is None:
            raise GatewayNotFoundError(f"resource {resource_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"resource_id": str(resource_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated, _ = self._resource_service.restore_within(unit_of_work, resource_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="resource",
                entity_id=resource_id,
                action=MutationAction.RESTORED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"resource": ResourceDTO.from_domain(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def delete_resource(
        self,
        resource_id: ResourceId,
        *,
        destructive: bool,
        confirm_id: str | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        self._require_destructive_confirmation(str(resource_id), destructive, confirm_id)
        existing = self._resources.get(resource_id)
        if existing is None:
            raise GatewayNotFoundError(f"resource {resource_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"resource_id": str(resource_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._resource_service.delete_within(unit_of_work, resource_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="resource",
                entity_id=resource_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"resource_id": resource_id, "recoverable": False}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def restore_node(
        self, node_id: NodeId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"node {node_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"node_id": str(node_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_node",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            updated = self._node_service.restore_within(unit_of_work, node_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="node",
                entity_id=node_id,
                action=MutationAction.RESTORED,
                before_state=existing.model_dump(mode="json"),
                after_state=updated.model_dump(mode="json"),
            )
            result_payload = {"node": NodeDTO.from_domain(updated).model_dump(mode="json")}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="restore_node",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def delete_node(
        self,
        node_id: NodeId,
        *,
        destructive: bool,
        confirm_id: str | None,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        self._require_destructive_confirmation(str(node_id), destructive, confirm_id)
        existing = self._nodes.get(node_id)
        if existing is None:
            raise GatewayNotFoundError(f"node {node_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"node_id": str(node_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_node",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._node_service.delete_within(unit_of_work, node_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="node",
                entity_id=node_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"node_id": node_id, "recoverable": False}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="delete_node",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def disconnect_edge(
        self, edge_id: EdgeId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        existing = self._edges.get(edge_id)
        if existing is None:
            raise GatewayNotFoundError(f"edge {edge_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"edge_id": str(edge_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="disconnect_edge",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            self._edge_service.disconnect_within(unit_of_work, edge_id)
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="edge",
                entity_id=edge_id,
                action=MutationAction.DELETED,
                before_state=existing.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {"edge_id": edge_id}
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="disconnect_edge",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    # ------------------------------------------------------------------
    # ST-09: enrichment / ingestion jobs / relation review / capture
    # ------------------------------------------------------------------

    def enrich_resource(
        self, resource_id: ResourceId, *, actor_name: str, reason: str, request_id: str
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if self._enrichment_service is None:
            raise GatewayValidationError(
                "enrichment is not configured on this instance (no provider)"
            )
        if self._extraction_service is None:
            raise GatewayValidationError("enrichment extraction is not configured on this instance")
        existing = self._resources.get(resource_id)
        if existing is None:
            raise GatewayNotFoundError(f"resource {resource_id} does not exist")
        workspace_id = existing.workspace_id
        payload = {"resource_id": str(resource_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="enrich_resource",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
        # `enrich_resource` manages its own read/classify/write transactions on this same
        # connection (S9-F02), so it must run with NO open transaction -- nesting a second
        # `BEGIN` on the shared connection raises `cannot start a transaction within a
        # transaction`. The receipt lookup above used a short, already-closed UoW.
        extracted = self._extraction_service.extract(
            resource_kind=existing.kind,
            canonical_identifier=existing.canonical_identifier,
            source_url=existing.source_url,
        )
        outcome = self._enrichment_service.enrich_resource(
            workspace_id=workspace_id,
            resource_id=resource_id,
            extracted=extracted,
            actor=actor_name,
        )
        result_payload: dict[str, Any] = {
            "profile_id": outcome.profile.id,
            "version_number": outcome.version.version_number,
        }
        with self._unit_of_work_factory() as unit_of_work:
            self._record_event(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="resource",
                entity_id=resource_id,
                action=MutationAction.UPDATED,
            )
            self._save_receipt(
                unit_of_work,
                workspace_id=workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="enrich_resource",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def list_ingestion_jobs(
        self, workspace_id: WorkspaceId, *, limit: int
    ) -> tuple[IngestionJobDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        with self._unit_of_work_factory() as unit_of_work:
            jobs = unit_of_work.ingestion_jobs.list_by_workspace(workspace_id)
        return tuple(IngestionJobDTO.from_domain(job) for job in jobs[:limit])

    def get_ingestion_job(self, job_id: IngestionJobId) -> IngestionJobDTO:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.ingestion_jobs.get(job_id)
        if job is None:
            raise GatewayNotFoundError(f"ingestion job {job_id} does not exist")
        return IngestionJobDTO.from_domain(job)

    def list_relation_proposals(
        self, workspace_id: WorkspaceId, *, status: RelationProposalStatus | None, limit: int
    ) -> tuple[RelationProposalDTO, ...]:
        _validate_limit(limit, maximum=MAX_LIST_LIMIT)
        self._require_workspace(workspace_id)
        with self._unit_of_work_factory() as unit_of_work:
            proposals = unit_of_work.relation_proposals.list_by_workspace(
                workspace_id, status=status
            )
        return tuple(RelationProposalDTO.from_domain(proposal) for proposal in proposals[:limit])

    def accept_relation_proposal(
        self,
        proposal_id: RelationProposalId,
        *,
        target_node_id: NodeId,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        payload = {
            "proposal_id": str(proposal_id),
            "target_node_id": str(target_node_id),
            "reason": reason,
        }
        with self._unit_of_work_factory() as unit_of_work:
            proposal = unit_of_work.relation_proposals.get(proposal_id)
            if proposal is None:
                raise GatewayNotFoundError(f"relation proposal {proposal_id} does not exist")
            if proposal.status is not RelationProposalStatus.NEEDS_REVIEW:
                raise GatewayConflictError(
                    f"relation proposal {proposal_id} is already resolved "
                    f"(status={proposal.status.value})"
                )
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="accept_relation_proposal",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            workspace = unit_of_work.workspaces.get(proposal.workspace_id)
            if workspace is None:
                raise GatewayNotFoundError(f"workspace {proposal.workspace_id} does not exist")
            edge_type = next(
                (
                    edge_type
                    for edge_type in workspace.edge_types
                    if edge_type.name == proposal.relation_kind.value
                ),
                None,
            )
            if edge_type is None:
                raise GatewayValidationError(
                    f"workspace has no edge type named {proposal.relation_kind.value}"
                )
            self._edge_service.connect_within(
                unit_of_work,
                proposal.workspace_id,
                edge_type.id,
                proposal.source_node_id,
                target_node_id,
            )
            resolved = proposal.model_copy(
                update={
                    "resolved_target_node_id": target_node_id,
                    "status": RelationProposalStatus.AUTO_APPLIED,
                }
            )
            unit_of_work.relation_proposals.save_without_commit(resolved)
            self._record_event(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="relation_proposal",
                entity_id=proposal_id,
                action=MutationAction.UPDATED,
                before_state=proposal.model_dump(mode="json"),
                after_state=resolved.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "proposal": RelationProposalDTO.from_domain(resolved).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="accept_relation_proposal",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def reject_relation_proposal(
        self,
        proposal_id: RelationProposalId,
        *,
        actor_name: str,
        reason: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        payload = {"proposal_id": str(proposal_id), "reason": reason}
        with self._unit_of_work_factory() as unit_of_work:
            proposal = unit_of_work.relation_proposals.get(proposal_id)
            if proposal is None:
                raise GatewayNotFoundError(f"relation proposal {proposal_id} does not exist")
            if proposal.status is RelationProposalStatus.AUTO_APPLIED:
                raise GatewayConflictError(
                    f"relation proposal {proposal_id} is already applied and cannot be rejected"
                )
            if proposal.status is RelationProposalStatus.REJECTED:
                result_payload = {
                    "proposal": RelationProposalDTO.from_domain(proposal).model_dump(mode="json")
                }
                return {**result_payload, "replayed": True}
            fingerprint, cached = self._lookup_receipt(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reject_relation_proposal",
                payload=payload,
            )
            if cached is not None:
                return {**cached, "replayed": True}
            resolved = proposal.model_copy(update={"status": RelationProposalStatus.REJECTED})
            unit_of_work.relation_proposals.save_without_commit(resolved)
            self._record_event(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                reason=reason,
                request_id=request_id,
                entity_type="relation_proposal",
                entity_id=proposal_id,
                action=MutationAction.UPDATED,
                before_state=proposal.model_dump(mode="json"),
                after_state=resolved.model_dump(mode="json"),
            )
            result_payload: dict[str, Any] = {
                "proposal": RelationProposalDTO.from_domain(resolved).model_dump(mode="json")
            }
            self._save_receipt(
                unit_of_work,
                workspace_id=proposal.workspace_id,
                actor_name=actor_name,
                request_id=request_id,
                operation="reject_relation_proposal",
                fingerprint=fingerprint,
                result_payload=result_payload,
            )
        return {**result_payload, "replayed": False}

    def capture(
        self,
        workspace_id: WorkspaceId,
        *,
        source: str,
        request_id: str,
        actor_name: str,
        payload_kind: str,
        url: str | None,
        text: str | None,
        intent: str,
        title: str | None,
        repository_node_id: NodeId | None,
        reason: str,
        operations: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        actor_name, reason, request_id = self._validate_attribution(actor_name, reason, request_id)
        if self._capture_planning_orchestrator is None:
            raise GatewayValidationError("capture/plan is not configured on this instance")
        from personal_graph_os.domain.capture import (
            CaptureEnvelope,
            CaptureIntent,
            CaptureOperation,
            CaptureOperationKind,
            CapturePayloadKind,
        )

        envelope = CaptureEnvelope(
            workspace_id=workspace_id,
            source=source,
            request_id=request_id,
            actor_name=actor_name,
            payload_kind=CapturePayloadKind(payload_kind),
            url=url,
            text=text,
            intent=CaptureIntent(intent),
            title=title,
            operations=tuple(
                CaptureOperation(kind=CaptureOperationKind(value)) for value in operations
            ),
        )
        outcome, _ = self._capture_planning_orchestrator.submit(
            envelope, repository_node_id=repository_node_id
        )
        return {
            "ingestion_job_id": outcome.job.id,
            "resource_id": outcome.resource_id,
            "document_id": outcome.document_id,
            "needs_clarification": outcome.needs_clarification,
            "was_replayed": outcome.was_replayed,
        }

    def import_clickup_item(
        self,
        workspace_id: WorkspaceId,
        *,
        task_id: str,
        intent: str,
        actor_name: str,
        repository_node_id: NodeId | None,
        reason: str,
    ) -> dict[str, Any]:
        """Import one user-selected ClickUp task through the shared capture pipeline (ST-10).

        `task_id` is both the stable external id and the idempotency key (`request_id ==
        task.id`), so an MCP caller gets the same replay/conflict semantics as every other
        channel without inventing a separate request id.
        """
        actor_name, reason, _request_id = self._validate_attribution(actor_name, reason, task_id)
        if self._clickup_service is None:
            raise GatewayValidationError("clickup import is not configured on this instance")
        from personal_graph_os.domain.capture import CaptureIntent

        outcome, plan_outcome = self._clickup_service.import_item(
            workspace_id,
            task_id=task_id,
            intent=CaptureIntent(intent),
            actor_name=actor_name,
            repository_node_id=repository_node_id,
        )
        result: dict[str, Any] = {
            "ingestion_job_id": outcome.job.id,
            "resource_id": outcome.resource_id,
            "document_id": outcome.document_id,
            "needs_clarification": outcome.needs_clarification,
            "was_replayed": outcome.was_replayed,
            "pending_operations": [
                operation.kind.value for operation in outcome.pending_operations
            ],
        }
        if plan_outcome is not None:
            result["plan"] = {
                "epic_id": plan_outcome.epic.id,
                "story_ids": [story.id for story in plan_outcome.stories],
                "task_ids": [task.id for task in plan_outcome.tasks],
                "plan_document_id": plan_outcome.plan_document.id,
            }
        return result
