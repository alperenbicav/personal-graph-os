"""Atomic multi-table boundary for the research aggregate (Node + Resource + DiscoveryRun) and
for a workflow-chain step (Node + Edge).

`NodeRepository`, `ResourceRepository`, `EdgeRepository`, and `DiscoveryRunRepository` each
commit their own single-table write. Creating a Resource always writes its backing Node in the
same operation, importing a batch of discovery candidates writes many Node/Resource pairs plus
one DiscoveryRun record, and advancing the guided workflow chain (ST-04.4) writes a new semantic
Node together with the edge connecting it to the prior step — none of these may leave a partially
written aggregate behind if a later step fails. A `ResearchUnitOfWork` is a narrow port for
exactly that: entered once per top-level operation, with `savepoint()` nesting a partial-failure
boundary inside it so one invalid candidate in a batch does not roll back the candidates already
applied.

ST-06 reuses this same port for MCP mutations: an agent's write and its attributed
`ActivityEvent` (`activity_events`) must commit or roll back together, so `AgentGatewayService`
opens the identical unit of work rather than a second, parallel one.

ST-02 reuses it for the standalone-document aggregate (Document + its first DocumentVersion +
optional DocumentLink): `DocumentVersionRepository` deliberately exposes no single-table `save`,
so a document is never left without the version that holds its actual content. It also commits
the resulting `IngestionJob` in the same transaction as the Resource/Document it captured
(review finding S2-R01): a capture either fully lands -- entity and job together -- or nothing
does, so a retry after any crash always finds either a clean committed job or none at all, never
a partially written one to reconcile.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Protocol

from personal_graph_os.application.repositories import (
    ActivityEventRepository,
    AttachmentRepository,
    CanvasPlacementRepository,
    CanvasRepository,
    ContextPackRepository,
    DiscoveryRunRepository,
    DocumentLinkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    EdgeRepository,
    FileReferenceRepository,
    IdempotencyReceiptRepository,
    IngestionJobRepository,
    NodeRepository,
    RelationProposalRepository,
    ResearchSettingsRepository,
    ResourceEnrichmentProfileRepository,
    ResourceEnrichmentProfileVersionRepository,
    ResourceRepository,
    SavedViewRepository,
    WorkspaceRepository,
)


class ResearchUnitOfWork(Protocol):
    # Read-only properties (not plain attributes): a Protocol attribute is invariant, which
    # would reject any concrete repository implementation that is merely structurally
    # compatible rather than the exact same class; a property is covariant instead.
    @property
    def workspaces(self) -> WorkspaceRepository: ...

    @property
    def nodes(self) -> NodeRepository: ...

    @property
    def resources(self) -> ResourceRepository: ...

    @property
    def edges(self) -> EdgeRepository: ...

    @property
    def canvases(self) -> CanvasRepository: ...

    @property
    def placements(self) -> CanvasPlacementRepository: ...

    @property
    def saved_views(self) -> SavedViewRepository: ...

    @property
    def research_settings(self) -> ResearchSettingsRepository: ...

    @property
    def attachments(self) -> AttachmentRepository: ...

    @property
    def file_references(self) -> FileReferenceRepository: ...

    @property
    def discovery_runs(self) -> DiscoveryRunRepository: ...

    @property
    def activity_events(self) -> ActivityEventRepository: ...

    @property
    def idempotency_receipts(self) -> IdempotencyReceiptRepository: ...

    @property
    def context_packs(self) -> ContextPackRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def document_versions(self) -> DocumentVersionRepository: ...

    @property
    def document_links(self) -> DocumentLinkRepository: ...

    @property
    def ingestion_jobs(self) -> IngestionJobRepository: ...

    @property
    def resource_enrichment_profiles(self) -> ResourceEnrichmentProfileRepository: ...

    @property
    def resource_enrichment_profile_versions(
        self,
    ) -> ResourceEnrichmentProfileVersionRepository: ...

    @property
    def relation_proposals(self) -> RelationProposalRepository: ...

    def __enter__(self) -> ResearchUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on a clean exit; roll back the entire unit of work on any exception."""

    def savepoint(self) -> AbstractContextManager[None]:
        """A nested boundary: release on success, roll back to it (not the whole unit) on
        failure, so one candidate's failure inside a batch never undoes prior candidates."""
        ...
