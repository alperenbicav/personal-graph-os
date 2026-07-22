"""Typed, JSON-friendly DTOs returned by MCP tools.

Kept separate from domain models so the wire contract (`pgos_*` tool outputs) can stay
stable even if internal domain models grow fields the protocol should not expose, and so
no domain object accidentally leaks a server-only detail (e.g. a managed storage path).
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from personal_graph_os.application.context_pack_service import ContextPackMaterialization
from personal_graph_os.application.discovery import DiscoveryCandidatePreview, DiscoveryPreview
from personal_graph_os.domain.activity import ActivityEvent, DiscoveredCandidate, DiscoveryRun
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.schema import Workspace
from personal_graph_os.domain.search import SearchResult
from personal_graph_os.domain.views import ContextPack

# Kept in sync with `gateway.MAX_SERIALIZED_FIELD_BYTES`: a read must honor the same bound a
# write enforces, so a row created before this bound existed (or written directly via REST)
# cannot make an MCP read return unbounded content (ST06-F04).
_MAX_SERIALIZED_FIELD_BYTES = 64 * 1024
_TRUNCATION_MARKER = "…[truncated]"
# A defensive recursion cap for a pre-existing/legacy `field_values` shape: real callers are
# already bounded shallow by the write-side validator, so this only guards a maliciously or
# accidentally deep nested structure written before that bound existed.
_MAX_JSON_DEPTH = 20


def _json_dumps(value: object) -> str:
    """The single canonical JSON rendering used both to measure and to bound content, so a
    byte count computed here always matches what actually goes over the wire. `ensure_ascii`
    is off so multibyte characters count as their real UTF-8 width rather than inflating into
    `\\uXXXX` escapes; quotes and backslash/control-character escaping are still applied,
    which is exactly the overhead ST06-F04's exact-bound contract must include (a bound on
    raw string bytes alone undercounts the true serialized size)."""
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def _serialized_bytes(value: object) -> int:
    return len(_json_dumps(value).encode("utf-8"))


def _bounded_text(value: str, *, maximum_bytes: int = _MAX_SERIALIZED_FIELD_BYTES) -> str:
    """Truncate `value` so its *serialized* JSON form -- quotes and escaping included, not
    merely the raw string's UTF-8 byte length -- never exceeds `maximum_bytes`.

    A prior version bounded only the raw encoded string, so quote/escape overhead (or, with
    `ensure_ascii=True`, `\\uXXXX`-expanded multibyte characters) could still push the
    serialized output past the exact 64 KiB contract even though the raw content fit
    (ST06-F04: independent flat/nested/collection probes measured 515-198 bytes over budget).
    Binary search is over character count, never a byte offset, so a multibyte UTF-8
    character can never be split.
    """
    if _serialized_bytes(value) <= maximum_bytes:
        return value
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if _serialized_bytes(value[:mid] + _TRUNCATION_MARKER) <= maximum_bytes:
            low = mid
        else:
            high = mid - 1
    result = value[:low] + _TRUNCATION_MARKER
    while result and _serialized_bytes(result) > maximum_bytes:
        result = result[:-1]
    return result


def _bounded_json_value(value: object, *, remaining_budget: list[int], depth: int = 0) -> object:
    """Recursively bound a JSON-like value against a single shared byte budget (ST06-F04):
    the previous `_bounded_field_values` only truncated direct string values, so nested
    objects/arrays and many individually-small values could still add up to an unbounded
    total. Every string is charged the *serialized* byte length it actually contributes
    (quotes/escaping included via `_serialized_bytes`, not a raw-byte undercount); once
    exhausted, trailing dict keys/list items are dropped entirely (a deterministic,
    always-valid-JSON omission, never a malformed partial value) instead of emitting ever
    more over-budget content.
    """
    if depth > _MAX_JSON_DEPTH:
        remaining_budget[0] -= _serialized_bytes(_TRUNCATION_MARKER)
        return _TRUNCATION_MARKER
    if isinstance(value, str):
        bounded = _bounded_text(value, maximum_bytes=max(remaining_budget[0], 0))
        remaining_budget[0] -= _serialized_bytes(bounded)
        return bounded
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if remaining_budget[0] <= 0:
                break
            # Charge the key plus its `":"`/`","` JSON punctuation before the value, so the
            # budget reflects the real marginal cost of adding one more entry, not just the
            # value content.
            remaining_budget[0] -= _serialized_bytes(key) + 2
            if remaining_budget[0] <= 0:
                break
            result[key] = _bounded_json_value(
                item, remaining_budget=remaining_budget, depth=depth + 1
            )
        return result
    if isinstance(value, list):
        bounded_list: list[object] = []
        for item in value:
            if remaining_budget[0] <= 0:
                break
            remaining_budget[0] -= 1  # the `","` separator
            bounded_list.append(
                _bounded_json_value(item, remaining_budget=remaining_budget, depth=depth + 1)
            )
        return bounded_list
    remaining_budget[0] -= _serialized_bytes(value)
    return value


def _shrink_to_fit(container: dict[str, object] | list[object], maximum_bytes: int) -> object:
    """Final safety net: the single-pass greedy budget above approximates the true combined
    serialized size of a nested structure but is not an exact global optimizer. Drop trailing
    entries (deterministically, in the same order already used for omission) until the
    measured serialized form actually satisfies the exact contract, guaranteeing the bound
    regardless of any approximation drift.
    """
    if isinstance(container, dict):
        items = list(container.items())
        while items and _serialized_bytes(dict(items)) > maximum_bytes:
            items.pop()
        return dict(items)
    values = list(container)
    while values and _serialized_bytes(values) > maximum_bytes:
        values.pop()
    return values


def _bounded_field_values(field_values: dict[str, object]) -> dict[str, object]:
    budget = [_MAX_SERIALIZED_FIELD_BYTES]
    bounded = _bounded_json_value(field_values, remaining_budget=budget)
    assert isinstance(bounded, dict)
    shrunk = _shrink_to_fit(bounded, _MAX_SERIALIZED_FIELD_BYTES)
    assert isinstance(shrunk, dict)
    return shrunk


def _bounded_text_collection(values: tuple[str, ...]) -> tuple[str, ...]:
    budget = [_MAX_SERIALIZED_FIELD_BYTES]
    bounded = _bounded_json_value(list(values), remaining_budget=budget)
    assert isinstance(bounded, list)
    shrunk = _shrink_to_fit(bounded, _MAX_SERIALIZED_FIELD_BYTES)
    assert isinstance(shrunk, list)
    return tuple(shrunk)


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
            body=_bounded_text(node.body),
            status_id=node.status_id,
            field_values=_bounded_field_values(dict(node.field_values)),
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
            field_values=_bounded_field_values(dict(edge.field_values)),
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
            next_action=(
                _bounded_text(resource.next_action) if resource.next_action is not None else None
            ),
            next_action_dismissed=resource.next_action_dismissed,
            open_questions=_bounded_text_collection(resource.open_questions),
            takeaways=_bounded_text_collection(resource.takeaways),
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


class ContextPackDTO(BaseModel):
    id: str
    workspace_id: str
    name: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    evidence_pointers: tuple[str, ...]
    inclusion_reasons: dict[str, str]
    object_limit: int
    token_limit: int | None
    created_at: datetime

    @classmethod
    def from_domain(cls, context_pack: ContextPack) -> ContextPackDTO:
        return cls(
            id=context_pack.id,
            workspace_id=context_pack.workspace_id,
            name=context_pack.name,
            node_ids=context_pack.node_ids,
            edge_ids=context_pack.edge_ids,
            evidence_pointers=context_pack.evidence_pointers,
            inclusion_reasons=context_pack.inclusion_reasons,
            object_limit=context_pack.object_limit,
            token_limit=context_pack.token_limit,
            created_at=context_pack.created_at,
        )


class MaterializedContextPackDTO(BaseModel):
    context_pack: ContextPackDTO
    nodes: tuple[NodeDTO, ...]
    resources: tuple[ResourceDTO, ...]
    edges: tuple[EdgeDTO, ...]
    evidence: tuple[EvidencePointerDTO, ...]
    archived_node_ids: tuple[str, ...]
    missing_node_ids: tuple[str, ...]
    missing_edge_ids: tuple[str, ...]
    unresolved_evidence_pointers: tuple[str, ...]
    omitted_for_token_budget: tuple[str, ...]
    estimated_tokens: int
    token_estimate_version: str

    @classmethod
    def from_domain(cls, materialization: ContextPackMaterialization) -> MaterializedContextPackDTO:
        evidence: list[EvidencePointerDTO] = []
        for member in materialization.evidence:
            if member.attachment is not None:
                evidence.append(EvidencePointerDTO.from_attachment(member.attachment))
            elif member.file_reference is not None:
                evidence.append(EvidencePointerDTO.from_file_reference(member.file_reference))
        return cls(
            context_pack=ContextPackDTO.from_domain(materialization.context_pack),
            nodes=tuple(NodeDTO.from_domain(node) for node in materialization.nodes),
            resources=tuple(
                ResourceDTO.from_domain(resource)
                for resource in materialization.resources_by_node_id.values()
            ),
            edges=tuple(EdgeDTO.from_domain(edge) for edge in materialization.edges),
            evidence=tuple(evidence),
            archived_node_ids=materialization.archived_node_ids,
            missing_node_ids=materialization.missing_node_ids,
            missing_edge_ids=materialization.missing_edge_ids,
            unresolved_evidence_pointers=materialization.unresolved_evidence_pointers,
            omitted_for_token_budget=materialization.omitted_for_token_budget,
            estimated_tokens=materialization.estimated_tokens,
            token_estimate_version=materialization.token_estimate_version,
        )


class ActivityEventSummaryDTO(BaseModel):
    """One list-row summary -- never the before/after snapshot (ST-07.1 plan: detail alone
    returns bounded snapshots)."""

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

    @classmethod
    def from_domain(cls, event: ActivityEvent) -> ActivityEventSummaryDTO:
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
        )


class ActivityEventDTO(ActivityEventSummaryDTO):
    """Full detail, including bounded before/after snapshots."""

    before_state: dict[str, object] | None
    after_state: dict[str, object] | None

    @classmethod
    def from_domain(cls, event: ActivityEvent) -> ActivityEventDTO:
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
            before_state=(
                _bounded_field_values(dict(event.before_state))
                if event.before_state is not None
                else None
            ),
            after_state=(
                _bounded_field_values(dict(event.after_state))
                if event.after_state is not None
                else None
            ),
        )


class ActivityEventPageDTO(BaseModel):
    events: tuple[ActivityEventSummaryDTO, ...]
    next_cursor: str | None
