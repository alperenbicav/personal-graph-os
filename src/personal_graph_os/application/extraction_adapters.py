"""Extraction adapter protocols: the only fetch/parse/metadata boundary `ExtractionService`
depends on (EP-2026-012 ST-03).

Concrete implementations (HTTP fetching, HTML/PDF parsing, DOI/arXiv/GitHub metadata lookups)
live in `personal_graph_os.infrastructure.extraction` and are resolved once at the composition
boundary, mirroring `application/repositories.py`.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.extraction import ExtractedContent
from personal_graph_os.domain.resource import ResourceKind


class FetchedResource(BaseModel):
    """The raw result of one successful fetch: exactly what a content parser needs, never
    more -- headers or transport metadata beyond content type/final URL are not modeled here
    because no parser currently needs them."""

    requested_url: str
    final_url: str
    content_type: str
    body: bytes

    @field_validator("requested_url", "final_url", "content_type")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "FetchedResource field")
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError(f"FetchedResource.{field_name} must not be empty")
        return stripped

    model_config = {"frozen": True}


class ContentFetcher(Protocol):
    """Retrieves bytes from a source URL. A concrete implementation owns every network,
    redirect, size, and timeout safeguard; `ExtractionService` never touches a socket itself.

    Raises `personal_graph_os.domain.extraction.SourceAccessDeniedError` for an
    authentication/authorization failure and `SourceFetchFailedError` for any other retrieval
    failure (timeout, network error, unexpected status).
    """

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        """Fetch `url`, optionally sending `accept` as the `Accept` request header."""
        ...


class ContentParser(Protocol):
    """Turns one already-fetched resource into bounded structured evidence."""

    def supports(self, *, resource_kind: ResourceKind, content_type: str) -> bool:
        """Whether this parser can handle `content_type` for `resource_kind`."""
        ...

    def parse(
        self,
        *,
        resource_kind: ResourceKind,
        canonical_identifier: str,
        fetched: FetchedResource,
    ) -> ExtractedContent:
        """Parse `fetched` into `ExtractedContent`. Raises `ExtractionError` on failure."""
        ...


class MetadataAdapter(Protocol):
    """Looks up bibliographic/repository metadata for a canonical identifier through a
    source-specific API, independent of any generic content fetch/parse."""

    def supports(self, *, resource_kind: ResourceKind, canonical_identifier: str) -> bool:
        """Whether this adapter recognizes `canonical_identifier`."""
        ...

    def fetch_metadata(
        self, *, resource_kind: ResourceKind, canonical_identifier: str
    ) -> ExtractedContent:
        """Fetch and parse metadata into `ExtractedContent`. Raises `ExtractionError` on
        failure."""
        ...


__all__ = [
    "ContentFetcher",
    "ContentParser",
    "FetchedResource",
    "MetadataAdapter",
]
