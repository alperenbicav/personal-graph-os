"""ST-06.4: immutable, bounded Context Pack manifests for agent handoff.

A `ContextPack` is a reproducible *selection* (Decision #15, `WORK.md`): it names Nodes/Edges/
evidence pointers, never copies their content. Creation is the only write; there is no update,
so a pack's manifest can never drift after an agent starts relying on it. Materialization is a
separate, repeatable read that resolves the manifest against current canonical state, reporting
what changed (missing/archived/unresolved) rather than silently omitting it.
"""

from __future__ import annotations

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


class ContextPackNotFoundError(LookupError):
    """The referenced `ContextPack` does not exist."""


class WorkspaceNotFoundError(LookupError):
    """The referenced workspace does not exist."""


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
        inclusion reason.
        """
        self._require_workspace(workspace_id)
        context_pack = ContextPack(
            workspace_id=workspace_id,
            name=name,
            node_ids=tuple(NodeId(value) for value in self._dedupe_in_order(node_ids)),
            edge_ids=tuple(EdgeId(value) for value in self._dedupe_in_order(edge_ids)),
            evidence_pointers=self._dedupe_in_order(evidence_pointers),
            inclusion_reasons=dict(inclusion_reasons),
            object_limit=object_limit,
            token_limit=token_limit,
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

        omitted, estimated_tokens = self._apply_token_budget(
            context_pack.token_limit, nodes, edges, evidence
        )
        nodes = [node for node in nodes if node.id not in omitted]
        edges = [edge for edge in edges if edge.id not in omitted]
        evidence = [member for member in evidence if member.pointer not in omitted]
        resources_by_node_id = {
            node_id: resource
            for node_id, resource in resources_by_node_id.items()
            if node_id not in omitted
        }

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

    @staticmethod
    def _apply_token_budget(
        token_limit: int | None,
        nodes: list[Node],
        edges: list[Edge],
        evidence: list[EvidenceMember],
    ) -> tuple[tuple[str, ...], int]:
        """Deterministically omit members in manifest order once the running estimate would
        exceed `token_limit`; `None` means unbounded (only the total is still reported)."""
        running_total = 0
        omitted: list[str] = []
        for node in nodes:
            node_tokens = estimate_tokens(node.title) + estimate_tokens(node.body)
            if token_limit is not None and running_total + node_tokens > token_limit:
                omitted.append(node.id)
                continue
            running_total += node_tokens
        for edge in edges:
            edge_tokens = estimate_tokens(edge.edge_type_id)
            if token_limit is not None and running_total + edge_tokens > token_limit:
                omitted.append(edge.id)
                continue
            running_total += edge_tokens
        for member in evidence:
            display_name = (
                member.attachment.file_name
                if member.attachment is not None
                else (member.file_reference.repository_name or member.file_reference.machine_name)
                if member.file_reference is not None
                else ""
            )
            evidence_tokens = estimate_tokens(display_name)
            if token_limit is not None and running_total + evidence_tokens > token_limit:
                omitted.append(member.pointer)
                continue
            running_total += evidence_tokens
        return tuple(omitted), running_total
