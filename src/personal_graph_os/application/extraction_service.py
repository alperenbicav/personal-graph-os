"""Resolves the right metadata/content adapter for one resource and returns its bounded,
evidence-carrying extraction (EP-2026-012 ST-03).

`ExtractionService` is the only composition point that turns a resource kind, canonical
identifier, and optional source URL into `ExtractedContent`: callers depend on this one
interface, never on a specific metadata or content adapter directly (code-design.md's
provider/strategy dispatch rule). A metadata adapter (DOI/arXiv/GitHub) always takes precedence
over a generic content fetch, since it returns richer, purpose-built bibliographic/repository
fields from an authoritative API rather than page/document scraping.
"""

from __future__ import annotations

from collections.abc import Sequence

from personal_graph_os.application.extraction_adapters import (
    ContentFetcher,
    ContentParser,
    MetadataAdapter,
)
from personal_graph_os.domain.extraction import ExtractedContent, UnsupportedSourceError
from personal_graph_os.domain.resource import ResourceKind


class ExtractionService:
    def __init__(
        self,
        *,
        metadata_adapters: Sequence[MetadataAdapter],
        content_parsers: Sequence[ContentParser],
        content_fetcher: ContentFetcher,
    ) -> None:
        self._metadata_adapters = tuple(metadata_adapters)
        self._content_parsers = tuple(content_parsers)
        self._content_fetcher = content_fetcher

    def extract(
        self,
        *,
        resource_kind: ResourceKind,
        canonical_identifier: str,
        source_url: str | None,
    ) -> ExtractedContent:
        """Return bounded structured evidence for one resource.

        Raises `personal_graph_os.domain.extraction.ExtractionError` (or a more specific
        subclass) when no adapter supports the resource, the source denies access, or the
        source cannot be retrieved -- never a partial or best-effort result.
        """
        metadata_adapter = self._select_metadata_adapter(resource_kind, canonical_identifier)
        if metadata_adapter is not None:
            return metadata_adapter.fetch_metadata(
                resource_kind=resource_kind, canonical_identifier=canonical_identifier
            )

        if source_url is None:
            raise UnsupportedSourceError(
                f"no metadata adapter recognizes {canonical_identifier!r} and no source URL "
                "was supplied to fall back to a content fetch"
            )

        fetched = self._content_fetcher.fetch(source_url)
        content_parser = self._select_content_parser(resource_kind, fetched.content_type)
        if content_parser is None:
            raise UnsupportedSourceError(
                f"no content parser supports content type {fetched.content_type!r} for "
                f"resource kind {resource_kind}"
            )
        return content_parser.parse(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            fetched=fetched,
        )

    def _select_metadata_adapter(
        self, resource_kind: ResourceKind, canonical_identifier: str
    ) -> MetadataAdapter | None:
        for adapter in self._metadata_adapters:
            if adapter.supports(
                resource_kind=resource_kind, canonical_identifier=canonical_identifier
            ):
                return adapter
        return None

    def _select_content_parser(
        self, resource_kind: ResourceKind, content_type: str
    ) -> ContentParser | None:
        for parser in self._content_parsers:
            if parser.supports(resource_kind=resource_kind, content_type=content_type):
                return parser
        return None
