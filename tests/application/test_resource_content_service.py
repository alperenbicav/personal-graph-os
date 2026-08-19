from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.application.resource_content_service import (
    ResourceContentService,
    ResourceContentUnavailableError,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    ResourceNotFoundError,
    ResourceService,
    new_workspace,
)
from personal_graph_os.domain.extraction import (
    MAX_BODY_MARKDOWN_LENGTH,
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
)
from personal_graph_os.domain.identifiers import ResourceId
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.resource_content import ResourceContent
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResourceContentRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _evidence(adapter_name: str, reference: str) -> ExtractionEvidence:
    return ExtractionEvidence(
        kind=EvidenceKind.FETCHED_CONTENT,
        adapter_name=adapter_name,
        source_reference=reference,
        content_hash="b" * 64,
        byte_length=64,
        retrieved_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
    )


class _FakeExtractionService:
    """Returns a queued `ExtractedContent` per `extract` call: the service-under-test's own
    branching (persist, keep-existing, raise) is what these tests prove, not the separately
    tested ST-03 adapters."""

    def __init__(self, *results: ExtractedContent) -> None:
        self._results = list(results)
        self.calls = 0

    def extract(
        self, *, resource_kind: ResourceKind, canonical_identifier: str, source_url: str | None
    ) -> ExtractedContent:
        self.calls += 1
        return self._results.pop(0)


def _extracted(canonical_identifier: str, body: str | None) -> ExtractedContent:
    return ExtractedContent(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier=canonical_identifier,
        title="A Test Article",
        body_markdown=body,
        evidence=(_evidence("test-adapter", canonical_identifier),),
    )


def _setup(
    sqlite_connection: sqlite3.Connection, *results: ExtractedContent
) -> tuple[ResourceContentService, ResourceService, str]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    content_service = ResourceContentService(
        resource_repository,
        SqliteResourceContentRepository(sqlite_connection),
        _FakeExtractionService(*results),  # type: ignore[arg-type]
    )
    return content_service, resource_service, str(workspace.id)


def _create_resource(resource_service: ResourceService, workspace_id: str) -> str:
    resource, _ = resource_service.create_or_reuse(
        workspace_id,  # type: ignore[arg-type]
        "A Test Article",
        "https://example.test/article",
        kind=ResourceKind.ARTICLE,
    )
    return resource.id


def test_refresh_persists_extracted_body_with_provenance(
    sqlite_connection: sqlite3.Connection,
) -> None:
    content_service, resource_service, workspace_id = _setup(
        sqlite_connection, _extracted("https://example.test/article", "# Hello\n\nFull text.")
    )
    resource_id = _create_resource(resource_service, workspace_id)

    content = content_service.refresh_content(ResourceId(resource_id))

    assert content.body_markdown == "# Hello\n\nFull text."
    assert content.adapter_name == "test-adapter"
    assert content.retrieved_at == datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    assert len(content.content_hash) == 64
    persisted = content_service.get_content(ResourceId(resource_id))
    assert persisted is not None
    assert persisted.body_markdown == content.body_markdown


def test_refresh_replaces_the_prior_row_wholesale(
    sqlite_connection: sqlite3.Connection,
) -> None:
    content_service, resource_service, workspace_id = _setup(
        sqlite_connection,
        _extracted("https://example.test/article", "old body"),
        _extracted("https://example.test/article", "new body"),
    )
    resource_id = _create_resource(resource_service, workspace_id)

    content_service.refresh_content(ResourceId(resource_id))
    refreshed = content_service.refresh_content(ResourceId(resource_id))

    assert refreshed.body_markdown == "new body"
    assert content_service.get_content(ResourceId(resource_id)) is not None
    row_count = sqlite_connection.execute(
        "SELECT COUNT(*) AS count FROM resource_content WHERE resource_id = ?", (resource_id,)
    ).fetchone()["count"]
    assert row_count == 1


def test_metadata_only_extraction_keeps_the_existing_body(
    sqlite_connection: sqlite3.Connection,
) -> None:
    content_service, resource_service, workspace_id = _setup(
        sqlite_connection,
        _extracted("https://example.test/article", "stored body"),
        _extracted("https://example.test/article", None),
    )
    resource_id = _create_resource(resource_service, workspace_id)
    content_service.refresh_content(ResourceId(resource_id))

    kept = content_service.refresh_content(ResourceId(resource_id))

    assert kept.body_markdown == "stored body"


def test_metadata_only_extraction_without_existing_body_fails_visibly(
    sqlite_connection: sqlite3.Connection,
) -> None:
    content_service, resource_service, workspace_id = _setup(
        sqlite_connection, _extracted("https://example.test/article", None)
    )
    resource_id = _create_resource(resource_service, workspace_id)

    with pytest.raises(ResourceContentUnavailableError):
        content_service.refresh_content(ResourceId(resource_id))
    assert content_service.get_content(ResourceId(resource_id)) is None


def test_get_content_returns_none_when_never_fetched(
    sqlite_connection: sqlite3.Connection,
) -> None:
    content_service, resource_service, workspace_id = _setup(sqlite_connection)
    resource_id = _create_resource(resource_service, workspace_id)

    assert content_service.get_content(ResourceId(resource_id)) is None


def test_unknown_resource_raises_not_found(sqlite_connection: sqlite3.Connection) -> None:
    content_service, _, _ = _setup(sqlite_connection, _extracted("x", "body"))

    with pytest.raises(ResourceNotFoundError):
        content_service.get_content(ResourceId("missing"))
    with pytest.raises(ResourceNotFoundError):
        content_service.refresh_content(ResourceId("missing"))


def test_resource_content_domain_bounds() -> None:
    with pytest.raises(Exception, match="must not be empty"):
        ResourceContent(
            resource_id=ResourceId("r"),
            workspace_id="w",  # type: ignore[arg-type]
            body_markdown="   ",
            content_hash="c" * 64,
            adapter_name="adapter",
        )
    with pytest.raises(Exception, match="must not exceed"):
        ResourceContent(
            resource_id=ResourceId("r"),
            workspace_id="w",  # type: ignore[arg-type]
            body_markdown="x" * (MAX_BODY_MARKDOWN_LENGTH + 1),
            content_hash="c" * 64,
            adapter_name="adapter",
        )
