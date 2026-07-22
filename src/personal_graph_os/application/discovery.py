"""ST-04.5: side-effect-free discovery preview plus provenance-tracked, idempotent import.

Decision #6 (`WORK.md`): discovery is an import contract, not a web-search engine. Candidates
are always externally supplied — pasted manually today, handed in by an MCP agent in ST-06 —
and this service only canonicalizes, deduplicates, and records what happened to each one. It
never performs outbound network access.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from personal_graph_os.application.repositories import ResourceRepository, WorkspaceRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.services import ResourceService, WorkspaceNotFoundError
from personal_graph_os.domain.activity import DiscoveredCandidate, DiscoveryOutcome, DiscoveryRun
from personal_graph_os.domain.errors import DomainError
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId
from personal_graph_os.domain.resource import Resource, ResourceKind
from personal_graph_os.domain.resource_identity import canonicalize_resource_identity
from personal_graph_os.domain.schema import Workspace


class DiscoveryDecision(StrEnum):
    CREATE = "create"
    REUSE = "reuse"
    REJECT = "reject"


class DiscoveryCandidateInput(BaseModel):
    """One caller-supplied candidate: a raw URL/identifier plus the evidence for it."""

    identifier: str
    title: str
    kind: ResourceKind | None = None
    description: str = ""
    evidence: tuple[str, ...] = ()


class DiscoveryCandidatePreview(BaseModel):
    """`preview()`'s read-only per-candidate verdict."""

    candidate: DiscoveryCandidateInput
    canonical_identifier: str | None
    decision: DiscoveryDecision
    reason: str
    existing_resource_id: ResourceId | None = None
    # Set only when `decision` is REUSE because an earlier candidate in this same preview
    # request already claims the identical canonical identity — not yet an existing DB resource.
    duplicate_of_candidate_index: int | None = None


class DiscoveryPreview(BaseModel):
    instruction: str
    sources_searched: tuple[str, ...]
    filters_interpreted: dict[str, object]
    candidates: tuple[DiscoveryCandidatePreview, ...]


class DiscoveryService:
    """Preview is pure and never writes; `apply` persists exactly one completed `DiscoveryRun`
    per call, importing/reusing candidates atomically with per-candidate savepoint isolation so
    one invalid candidate never rolls back the others in the same batch."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        resources: ResourceRepository,
        resource_service: ResourceService,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._workspaces = workspaces
        self._resources = resources
        self._resource_service = resource_service
        self._unit_of_work_factory = unit_of_work_factory

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def preview(
        self,
        workspace_id: WorkspaceId,
        instruction: str,
        candidates: Sequence[DiscoveryCandidateInput],
        *,
        sources_searched: Sequence[str] = (),
        filters_interpreted: dict[str, object] | None = None,
    ) -> DiscoveryPreview:
        self._require_workspace(workspace_id)
        # Tracks each canonical identity already seen earlier in *this* request (index into
        # `candidates`) so a later duplicate previews as reuse/deduplicated instead of a second,
        # materially false "create" — `apply()` only ever writes the first one (ST04-F06).
        seen_at_index: dict[str, int] = {}
        previewed: list[DiscoveryCandidatePreview] = []
        for index, candidate in enumerate(candidates):
            previewed.append(self._preview_candidate(workspace_id, candidate, index, seen_at_index))
        return DiscoveryPreview(
            instruction=instruction,
            sources_searched=tuple(sources_searched),
            filters_interpreted=dict(filters_interpreted or {}),
            candidates=tuple(previewed),
        )

    def _preview_candidate(
        self,
        workspace_id: WorkspaceId,
        candidate: DiscoveryCandidateInput,
        index: int,
        seen_at_index: dict[str, int],
    ) -> DiscoveryCandidatePreview:
        try:
            identity = canonicalize_resource_identity(candidate.identifier)
        except DomainError as error:
            return DiscoveryCandidatePreview(
                candidate=candidate,
                canonical_identifier=None,
                decision=DiscoveryDecision.REJECT,
                reason=str(error),
            )

        first_index = seen_at_index.get(identity.canonical_identifier)
        if first_index is not None:
            return DiscoveryCandidatePreview(
                candidate=candidate,
                canonical_identifier=identity.canonical_identifier,
                decision=DiscoveryDecision.REUSE,
                reason=f"duplicate of candidate #{first_index + 1} in this batch",
                duplicate_of_candidate_index=first_index,
            )
        seen_at_index[identity.canonical_identifier] = index

        existing = self._resources.get_by_canonical_identifier(
            workspace_id, identity.canonical_identifier
        )
        if existing is not None:
            return DiscoveryCandidatePreview(
                candidate=candidate,
                canonical_identifier=identity.canonical_identifier,
                decision=DiscoveryDecision.REUSE,
                reason="matches an existing resource by canonical identity",
                existing_resource_id=existing.id,
            )
        return DiscoveryCandidatePreview(
            candidate=candidate,
            canonical_identifier=identity.canonical_identifier,
            decision=DiscoveryDecision.CREATE,
            reason="new canonical identity",
        )

    def apply(
        self,
        workspace_id: WorkspaceId,
        agent_identity: str,
        instruction: str,
        candidates: Sequence[DiscoveryCandidateInput],
        *,
        sources_searched: Sequence[str] = (),
        filters_interpreted: dict[str, object] | None = None,
    ) -> DiscoveryRun:
        """Import `candidates` into one completed `DiscoveryRun`.

        Reapplying the same candidates is idempotent: each already canonicalizes to its
        existing resource (no new `Node`/`Resource` is written), so no duplicate resources are
        ever created, even across repeated calls with the same request.
        """
        self._require_workspace(workspace_id)
        recorded: list[DiscoveredCandidate] = []
        newly_created: list[tuple[Resource, Node]] = []

        with self._unit_of_work_factory() as unit_of_work:
            for candidate in candidates:
                try:
                    with unit_of_work.savepoint():
                        resource, was_created, node = self._resource_service.create_or_reuse_within(
                            unit_of_work,
                            workspace_id,
                            candidate.title,
                            candidate.identifier,
                            kind=candidate.kind,
                            body=candidate.description,
                        )
                except DomainError as error:
                    recorded.append(
                        DiscoveredCandidate(
                            raw_identifier=candidate.identifier,
                            title=candidate.title,
                            kind=candidate.kind,
                            description=candidate.description,
                            evidence=candidate.evidence,
                            outcome=DiscoveryOutcome.FAILED,
                            reason=str(error),
                        )
                    )
                    continue

                recorded.append(
                    DiscoveredCandidate(
                        raw_identifier=candidate.identifier,
                        canonical_identifier=resource.canonical_identifier,
                        title=candidate.title,
                        kind=candidate.kind,
                        description=candidate.description,
                        evidence=candidate.evidence,
                        outcome=DiscoveryOutcome.IMPORTED
                        if was_created
                        else DiscoveryOutcome.REUSED,
                        reason=None if was_created else "reused existing resource",
                        existing_resource_id=None if was_created else resource.id,
                        imported_node_id=resource.node_id,
                    )
                )
                if was_created and node is not None:
                    newly_created.append((resource, node))

            run = DiscoveryRun(
                workspace_id=workspace_id,
                agent_identity=agent_identity,
                instruction=instruction,
                sources_searched=tuple(sources_searched),
                filters_interpreted=dict(filters_interpreted or {}),
                candidates=tuple(recorded),
                completed_at=datetime.now(UTC),
            )
            unit_of_work.discovery_runs.save_without_commit(run)

        for resource, node in newly_created:
            self._resource_service.index_created_resource(resource, node)

        return run
