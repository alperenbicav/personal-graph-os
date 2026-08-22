"""SQLite implementation of `ResearchUnitOfWork` using explicit `BEGIN`/`SAVEPOINT`.

Concurrency model: the HTTP API's request connection serializes all request handling behind
one `anyio.Lock` (see `personal_graph_os.api.app`), and the Telegram poller writes through its
own connection. Cross-connection writers are serialized by SQLite itself (WAL journaling plus
the `busy_timeout` set in `open_connection`), so this adapter only has to defend against a
partially applied multi-table write when a later step in the same operation fails.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType

from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteActivityEventRepository,
    SqliteAttachmentRepository,
    SqliteCanvasPlacementRepository,
    SqliteCanvasRepository,
    SqliteChannelSyncStateRepository,
    SqliteCollectionRepository,
    SqliteContextPackRepository,
    SqliteDiscoveryRunRepository,
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteDocumentVersionRepository,
    SqliteEdgeRepository,
    SqliteFileReferenceRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqliteRelationProposalRepository,
    SqliteResearchSettingsRepository,
    SqliteResourceEnrichmentProfileRepository,
    SqliteResourceEnrichmentProfileVersionRepository,
    SqliteResourceRepository,
    SqliteSavedViewRepository,
    SqliteTagRepository,
    SqliteWorkItemChecklistItemRepository,
    SqliteWorkItemRepository,
    SqliteWorkPlanningReceiptRepository,
    SqliteWorkspaceRepository,
)


class SqliteResearchUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.workspaces = SqliteWorkspaceRepository(connection)
        self.nodes = SqliteNodeRepository(connection)
        self.resources = SqliteResourceRepository(connection)
        self.edges = SqliteEdgeRepository(connection)
        self.canvases = SqliteCanvasRepository(connection)
        self.placements = SqliteCanvasPlacementRepository(connection)
        self.saved_views = SqliteSavedViewRepository(connection)
        self.research_settings = SqliteResearchSettingsRepository(connection)
        self.attachments = SqliteAttachmentRepository(connection)
        self.file_references = SqliteFileReferenceRepository(connection)
        self.discovery_runs = SqliteDiscoveryRunRepository(connection)
        self.activity_events = SqliteActivityEventRepository(connection)
        self.idempotency_receipts = SqliteIdempotencyReceiptRepository(connection)
        self.context_packs = SqliteContextPackRepository(connection)
        self.collections = SqliteCollectionRepository(connection)
        self.tags = SqliteTagRepository(connection)
        self.documents = SqliteDocumentRepository(connection)
        self.document_versions = SqliteDocumentVersionRepository(connection)
        self.document_links = SqliteDocumentLinkRepository(connection)
        self.ingestion_jobs = SqliteIngestionJobRepository(connection)
        self.resource_enrichment_profiles = SqliteResourceEnrichmentProfileRepository(connection)
        self.resource_enrichment_profile_versions = (
            SqliteResourceEnrichmentProfileVersionRepository(connection)
        )
        self.relation_proposals = SqliteRelationProposalRepository(connection)
        self.work_items = SqliteWorkItemRepository(connection)
        self.work_item_checklist_items = SqliteWorkItemChecklistItemRepository(connection)
        self.work_planning_receipts = SqliteWorkPlanningReceiptRepository(connection)
        self.channel_sync_state = SqliteChannelSyncStateRepository(connection)
        self._savepoint_count = 0

    def __enter__(self) -> SqliteResearchUnitOfWork:
        self._connection.execute("BEGIN")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()

    @contextmanager
    def savepoint(self) -> Generator[None]:
        self._savepoint_count += 1
        name = f"research_uow_sp_{self._savepoint_count}"
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
