"""Persisted source content for the reading-first UI (EP-2026-012 ST-13).

`ResourceContentService` is the single write/read path for a resource's bounded source text:
a refresh runs the resource through `ExtractionService` (no LLM/provider required) and replaces
its one `resource_content` row, and the reader loads that row instead of re-fetching the
network on every open. Extraction failures propagate as the visible `ExtractionError` family --
the service never stores partial or best-effort content.
"""

from __future__ import annotations

from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.application.repositories import (
    ResourceContentRepository,
    ResourceRepository,
)
from personal_graph_os.application.services import ResourceNotFoundError
from personal_graph_os.domain.errors import DomainError
from personal_graph_os.domain.extraction import hash_content
from personal_graph_os.domain.identifiers import ResourceId
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.resource_content import ResourceContent


class ResourceContentService:
    def __init__(
        self,
        resources: ResourceRepository,
        contents: ResourceContentRepository,
        extraction: ExtractionService,
    ) -> None:
        self._resources = resources
        self._contents = contents
        self._extraction = extraction

    def get_content(self, resource_id: ResourceId) -> ResourceContent | None:
        """Return the persisted source content, or `None` when the source was never fetched."""
        self._get_resource_or_raise(resource_id)
        return self._contents.get_by_resource(resource_id)

    def refresh_content(self, resource_id: ResourceId) -> ResourceContent:
        """Fetch the resource's source through the extraction adapters and persist its bounded
        body, replacing any prior row. Raises `ExtractionError` (or a subclass) when no adapter
        supports the source or the fetch/parse fails."""
        resource = self._get_resource_or_raise(resource_id)
        extracted = self._extraction.extract(
            resource_kind=resource.kind,
            canonical_identifier=resource.canonical_identifier,
            source_url=resource.source_url,
        )
        if extracted.body_markdown is None:
            # A metadata-only adapter (e.g. a DOI lookup that returns no full text) must not
            # silently erase a previously stored body from a richer source.
            existing = self._contents.get_by_resource(resource_id)
            if existing is not None:
                return existing
            raise ResourceContentUnavailableError(
                f"extraction for resource {resource_id} produced no readable body text"
            )
        content = ResourceContent(
            resource_id=resource.id,
            workspace_id=resource.workspace_id,
            body_markdown=extracted.body_markdown,
            content_hash=hash_content(extracted.body_markdown),
            adapter_name=extracted.evidence[0].adapter_name,
            retrieved_at=extracted.evidence[0].retrieved_at,
        )
        self._contents.save(content)
        return content

    def _get_resource_or_raise(self, resource_id: ResourceId) -> Resource:
        resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceNotFoundError(f"resource {resource_id} does not exist")
        return resource


class ResourceContentNotFoundError(DomainError):
    """Raised when a resource exists but has no persisted source content yet."""


class ResourceContentUnavailableError(DomainError):
    """Raised when an extraction run completed but produced no readable body text (e.g. a
    metadata-only lookup), so there is nothing new to persist or show."""
