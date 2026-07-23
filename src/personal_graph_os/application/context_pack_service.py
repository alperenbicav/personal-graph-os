"""ST-06.4: immutable, bounded Context Pack manifests for agent handoff.

A `ContextPack` is a reproducible *selection* (Decision #15, `WORK.md`): it names Nodes/Edges/
evidence pointers, never copies their content. Creation is the only write; there is no update,
so a pack's manifest can never drift after an agent starts relying on it. Materialization is a
separate, repeatable read that resolves the manifest against current canonical state, reporting
what changed (missing/archived/unresolved) rather than silently omitting it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.repositories import (
    ContextPackRepository,
    EdgeRepository,
    NodeRepository,
    ResourceRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import (
    AttachmentId,
    ContextPackId,
    EdgeId,
    FileReferenceId,
    NodeId,
    WorkspaceId,
)
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.views import ContextPack

# A versioned, provider-neutral heuristic (~4 characters/token): never claims to match a
# specific provider's tokenizer (decision #17, `WORK.md`), only gives a deterministic budget.
TOKEN_ESTIMATE_VERSION = "chars-div-4-v1"


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _json_tokens(value: object) -> int:
    """Estimate tokens over the exact JSON shape a value serializes to on the wire, so the
    budget covers every field a caller can actually receive rather than a hand-picked subset
    (ST06-F03)."""
    return estimate_tokens(json.dumps(value, default=str, sort_keys=True))


def _evidence_member_wire_shape(member: EvidenceMember) -> dict[str, object]:
    """Mirrors `EvidencePointerDTO`'s fields without importing the MCP-layer DTO module."""
    if member.attachment is not None:
        attachment = member.attachment
        return {
            "pointer": member.pointer,
            "kind": "attachment",
            "display_name": attachment.file_name,
            "mime_type": attachment.mime_type,
            "is_verified": None,
            "created_at": attachment.created_at,
        }
    file_reference = member.file_reference
    if file_reference is not None:
        return {
            "pointer": member.pointer,
            "kind": "file_reference",
            "display_name": file_reference.repository_name or file_reference.machine_name,
            "mime_type": None,
            "is_verified": (
                file_reference.last_verified_at is not None and not file_reference.is_missing
            ),
            "created_at": file_reference.last_verified_at,
        }
    return {"pointer": member.pointer}


class ContextPackNotFoundError(LookupError):
    """The referenced `ContextPack` does not exist."""


class WorkspaceNotFoundError(LookupError):
    """The referenced workspace does not exist."""


class ContextPackSelectionError(ValueError):
    """A selected Node/Edge/evidence pointer does not exist, belongs to a different
    workspace, or uses an unsupported evidence pointer scheme (ST06-F02)."""


class ContextPackTokenBudgetError(ValueError):
    """`token_limit` is smaller than the pack's non-omittable manifest -- no selection could
    ever satisfy it, so creation is rejected instead of persisting a pack materialization can
    never bring under budget (ST06-F03)."""


@dataclass(frozen=True)
class EvidenceMember:
    """One resolved evidence pointer: exactly one of `attachment`/`file_reference` is set."""

    pointer: str
    attachment: Attachment | None = None
    file_reference: FileReference | None = None


@dataclass(frozen=True)
class ContextPackMaterialization:
    """A `ContextPack` manifest resolved against current canonical state."""

    context_pack: ContextPack
    nodes: tuple[Node, ...]
    resources_by_node_id: dict[str, Resource]
    edges: tuple[Edge, ...]
    evidence: tuple[EvidenceMember, ...]
    archived_node_ids: tuple[str, ...]
    missing_node_ids: tuple[str, ...]
    missing_edge_ids: tuple[str, ...]
    unresolved_evidence_pointers: tuple[str, ...]
    omitted_for_token_budget: tuple[str, ...]
    estimated_tokens: int
    token_estimate_version: str = TOKEN_ESTIMATE_VERSION


class ContextPackService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        resources: ResourceRepository,
        files: FileService,
        context_packs: ContextPackRepository,
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._resources = resources
        self._files = files
        self._context_packs = context_packs

    def _require_workspace(self, workspace_id: WorkspaceId) -> None:
        if self._workspaces.get(workspace_id) is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")

    def _require_context_pack(self, context_pack_id: ContextPackId) -> ContextPack:
        context_pack = self._context_packs.get(context_pack_id)
        if context_pack is None:
            raise ContextPackNotFoundError(f"context pack {context_pack_id} does not exist")
        return context_pack

    @staticmethod
    def _dedupe_in_order(values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return tuple(ordered)

    def _require_owned_node(self, workspace_id: WorkspaceId, node_id: NodeId) -> None:
        node = self._nodes.get(node_id)
        if node is None or node.workspace_id != workspace_id:
            raise ContextPackSelectionError(
                f"node {node_id} does not exist in workspace {workspace_id}"
            )

    def _require_owned_edge(self, workspace_id: WorkspaceId, edge_id: EdgeId) -> None:
        edge = self._edges.get(edge_id)
        if edge is None or edge.workspace_id != workspace_id:
            raise ContextPackSelectionError(
                f"edge {edge_id} does not exist in workspace {workspace_id}"
            )

    def _require_owned_evidence_pointer(self, workspace_id: WorkspaceId, pointer: str) -> None:
        """Reject an evidence pointer whose referenced object does not exist, whose scheme is
        unsupported, or whose owning Node belongs to a different workspace (ST06-F02). Only a
        pointer that resolved here and later disappears becomes `unresolved` at materialize
        time -- this check runs once, before persistence."""
        scheme, _, identifier = pointer.partition(":")
        owner_node_id: NodeId | None = None
        if scheme == "attachment" and identifier:
            attachment = self._files.get_attachment(AttachmentId(identifier))
            owner_node_id = attachment.node_id if attachment is not None else None
        elif scheme == "file-reference" and identifier:
            file_reference = self._files.get_file_reference(FileReferenceId(identifier))
            owner_node_id = file_reference.node_id if file_reference is not None else None
        if owner_node_id is None:
            raise ContextPackSelectionError(
                f"evidence pointer {pointer!r} does not exist or has an unsupported scheme"
            )
        self._require_owned_node(workspace_id, owner_node_id)

    def create_within(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        name: str,
        *,
        node_ids: Sequence[NodeId] = (),
        edge_ids: Sequence[EdgeId] = (),
        evidence_pointers: Sequence[str] = (),
        inclusion_reasons: dict[str, str],
        object_limit: int = 200,
        token_limit: int | None = None,
    ) -> ContextPack:
        """Create one immutable `ContextPack` inside a caller-owned, already-open transaction.

        IDs are deduplicated in caller order (decision: 06.4 plan) before validation, so a
        repeated identifier never counts twice against `object_limit` or requires a second
        inclusion reason. Every selected Node, Edge, and evidence pointer must exist and belong
        to `workspace_id` (ST06-F02); a cross-workspace or unresolved selection is rejected
        here rather than silently accepted and reported "missing" only at materialize time.
        """
        self._require_workspace(workspace_id)
        deduped_node_ids = tuple(NodeId(value) for value in self._dedupe_in_order(node_ids))
        deduped_edge_ids = tuple(EdgeId(value) for value in self._dedupe_in_order(edge_ids))
        deduped_evidence_pointers = self._dedupe_in_order(evidence_pointers)
        for node_id in deduped_node_ids:
            self._require_owned_node(workspace_id, node_id)
        for edge_id in deduped_edge_ids:
            self._require_owned_edge(workspace_id, edge_id)
        for pointer in deduped_evidence_pointers:
            self._require_owned_evidence_pointer(workspace_id, pointer)
        context_pack = ContextPack(
            workspace_id=workspace_id,
            name=name,
            node_ids=deduped_node_ids,
            edge_ids=deduped_edge_ids,
            evidence_pointers=deduped_evidence_pointers,
            inclusion_reasons=dict(inclusion_reasons),
            object_limit=object_limit,
            token_limit=token_limit,
        )
        if token_limit is not None:
            # The true non-omittable floor is not the manifest alone: if every selected
            # member ultimately has to be omitted, `omitted_for_token_budget` must still name
            # all of them, and that report itself can be too large to fit (ST06-F03 round-2:
            # a 120-node selection's manifest fit its limit, but the fully-omitted report
            # still didn't). Reject up front rather than persist a pack whose materialization
            # can never obey its own configured budget under any possible omission outcome.
            floor_tokens = _json_tokens(
                {
                    "context_pack": context_pack.model_dump(mode="json"),
                    "nodes": [],
                    "resources": [],
                    "edges": [],
                    "evidence": [],
                    "archived_node_ids": (),
                    "missing_node_ids": (),
                    "missing_edge_ids": (),
                    "unresolved_evidence_pointers": (),
                    "omitted_for_token_budget": (
                        *deduped_node_ids,
                        *deduped_edge_ids,
                        *deduped_evidence_pointers,
                    ),
                    "token_estimate_version": TOKEN_ESTIMATE_VERSION,
                }
            )
            if floor_tokens > token_limit:
                raise ContextPackTokenBudgetError(
                    f"token_limit {token_limit} is smaller than this pack's non-omittable "
                    f"floor ({floor_tokens} estimated tokens) -- the manifest plus a full "
                    "omission report of every selected member; increase token_limit or "
                    "reduce the selection/inclusion reasons"
                )
        unit_of_work.context_packs.save_without_commit(context_pack)
        return context_pack

    def get(self, context_pack_id: ContextPackId) -> ContextPack:
        return self._require_context_pack(context_pack_id)

    def list_by_workspace(self, workspace_id: WorkspaceId) -> tuple[ContextPack, ...]:
        self._require_workspace(workspace_id)
        return self._context_packs.list_by_workspace(workspace_id)

    def delete_within(
        self, unit_of_work: ResearchUnitOfWork, context_pack_id: ContextPackId
    ) -> ContextPack:
        context_pack = self._require_context_pack(context_pack_id)
        unit_of_work.context_packs.delete_without_commit(context_pack_id)
        return context_pack

    def _resolve_evidence(self, pointer: str) -> tuple[EvidenceMember | None, bool]:
        """Resolve one evidence pointer; the second element is `True` when the pointer's
        scheme is recognized but the referenced object no longer exists or the scheme itself
        is unsupported — either way, `unresolved`, never an error (decision #16, `WORK.md`)."""
        scheme, _, identifier = pointer.partition(":")
        if scheme == "attachment" and identifier:
            attachment = self._files.get_attachment(AttachmentId(identifier))
            if attachment is not None:
                return EvidenceMember(pointer=pointer, attachment=attachment), False
            return None, True
        if scheme == "file-reference" and identifier:
            file_reference = self._files.get_file_reference(FileReferenceId(identifier))
            if file_reference is not None:
                return EvidenceMember(pointer=pointer, file_reference=file_reference), False
            return None, True
        return None, True

    def materialize(self, context_pack_id: ContextPackId) -> ContextPackMaterialization:
        context_pack = self._require_context_pack(context_pack_id)

        nodes: list[Node] = []
        resources_by_node_id: dict[str, Resource] = {}
        archived_node_ids: list[str] = []
        missing_node_ids: list[str] = []
        for node_id in context_pack.node_ids:
            node = self._nodes.get(node_id)
            if node is None:
                missing_node_ids.append(node_id)
                continue
            nodes.append(node)
            if node.is_archived:
                archived_node_ids.append(node_id)
            resource = self._resources.get_by_node(node_id)
            if resource is not None:
                resources_by_node_id[node_id] = resource

        edges: list[Edge] = []
        missing_edge_ids: list[str] = []
        for edge_id in context_pack.edge_ids:
            edge = self._edges.get(edge_id)
            if edge is None:
                missing_edge_ids.append(edge_id)
                continue
            edges.append(edge)

        evidence: list[EvidenceMember] = []
        unresolved_evidence_pointers: list[str] = []
        for pointer in context_pack.evidence_pointers:
            resolved, unresolved = self._resolve_evidence(pointer)
            if resolved is not None:
                evidence.append(resolved)
            if unresolved:
                unresolved_evidence_pointers.append(pointer)

        def wire_shape(
            *,
            included_nodes: Sequence[Node],
            included_edges: Sequence[Edge],
            included_evidence: Sequence[EvidenceMember],
            omitted_ids: Sequence[str],
        ) -> dict[str, object]:
            return {
                "context_pack": context_pack.model_dump(mode="json"),
                "nodes": [node.model_dump(mode="json") for node in included_nodes],
                "resources": [
                    resources_by_node_id[node.id].model_dump(mode="json")
                    for node in included_nodes
                    if node.id in resources_by_node_id
                ],
                "edges": [edge.model_dump(mode="json") for edge in included_edges],
                "evidence": [_evidence_member_wire_shape(member) for member in included_evidence],
                "archived_node_ids": tuple(archived_node_ids),
                "missing_node_ids": tuple(missing_node_ids),
                "missing_edge_ids": tuple(missing_edge_ids),
                "unresolved_evidence_pointers": tuple(unresolved_evidence_pointers),
                "omitted_for_token_budget": tuple(omitted_ids),
                "token_estimate_version": TOKEN_ESTIMATE_VERSION,
            }

        # Decide omissions against the exact final wire shape at every step. Crucially, the
        # check for including item i also assumes every *not-yet-decided* later item is
        # omitted (`remaining_after`) -- not just the items already confirmed omitted so far.
        # Otherwise an early item can look artificially cheap to include (the ids that will
        # later be pushed into `omitted_for_token_budget` haven't been charged yet), and the
        # final wrapper -- once every later item really does get omitted -- can exceed
        # `token_limit` even though every individual decision looked correct in isolation
        # (ST06-F03 round-2: 120 large nodes, 119 ultimately omitted, one included early
        # while the budget still looked available; the final report was 4,861 tokens against
        # a 4,208 limit). `create_within` already guarantees the fully-omitted floor fits, and
        # omitting an item never changes the omitted-array's *content* versus assuming it was
        # always going to be omitted, so this check is both necessary and sufficient to keep
        # every prefix -- and therefore the final result -- within budget.
        included_nodes: list[Node] = []
        included_edges: list[Edge] = []
        included_evidence: list[EvidenceMember] = []
        omitted: list[str] = []
        if context_pack.token_limit is None:
            included_nodes = list(nodes)
            included_edges = list(edges)
            included_evidence = list(evidence)
        else:
            limit = context_pack.token_limit
            edge_ids_all = [edge.id for edge in edges]
            evidence_pointers_all = [member.pointer for member in evidence]
            for index, node in enumerate(nodes):
                remaining_after = (
                    [n.id for n in nodes[index + 1 :]] + edge_ids_all + evidence_pointers_all
                )
                included_nodes.append(node)
                candidate = wire_shape(
                    included_nodes=included_nodes,
                    included_edges=included_edges,
                    included_evidence=included_evidence,
                    omitted_ids=omitted + remaining_after,
                )
                if _json_tokens(candidate) > limit:
                    included_nodes.pop()
                    omitted.append(node.id)
            for index, edge in enumerate(edges):
                remaining_after = [e.id for e in edges[index + 1 :]] + evidence_pointers_all
                included_edges.append(edge)
                candidate = wire_shape(
                    included_nodes=included_nodes,
                    included_edges=included_edges,
                    included_evidence=included_evidence,
                    omitted_ids=omitted + remaining_after,
                )
                if _json_tokens(candidate) > limit:
                    included_edges.pop()
                    omitted.append(edge.id)
            for index, member in enumerate(evidence):
                remaining_after = [m.pointer for m in evidence[index + 1 :]]
                included_evidence.append(member)
                candidate = wire_shape(
                    included_nodes=included_nodes,
                    included_edges=included_edges,
                    included_evidence=included_evidence,
                    omitted_ids=omitted + remaining_after,
                )
                if _json_tokens(candidate) > limit:
                    included_evidence.pop()
                    omitted.append(member.pointer)

        nodes = included_nodes
        edges = included_edges
        evidence = included_evidence
        resources_by_node_id = {
            node_id: resource
            for node_id, resource in resources_by_node_id.items()
            if node_id not in omitted
        }

        # The final reported estimate is the true token cost of the complete returned shape --
        # identical in construction to the shape the loop above already validated against
        # `token_limit`, never a separately recomputed subset (ST06-F03).
        estimated_tokens = _json_tokens(
            wire_shape(
                included_nodes=nodes,
                included_edges=edges,
                included_evidence=evidence,
                omitted_ids=omitted,
            )
        )

        return ContextPackMaterialization(
            context_pack=context_pack,
            nodes=tuple(nodes),
            resources_by_node_id=resources_by_node_id,
            edges=tuple(edges),
            evidence=tuple(evidence),
            archived_node_ids=tuple(archived_node_ids),
            missing_node_ids=tuple(missing_node_ids),
            missing_edge_ids=tuple(missing_edge_ids),
            unresolved_evidence_pointers=tuple(unresolved_evidence_pointers),
            omitted_for_token_budget=tuple(omitted),
            estimated_tokens=estimated_tokens,
        )
