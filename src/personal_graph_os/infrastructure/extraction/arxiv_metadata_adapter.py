"""arXiv bibliographic metadata via the arXiv Atom export API (EP-2026-012 ST-03).

Recognizes only `canonical_identifier` values already resolved by
`domain.resource_identity.canonicalize_resource_identity` (an `arxiv:<...>` prefix).
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree

from pydantic import ValidationError

from personal_graph_os.application.extraction_adapters import ContentFetcher
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    MalformedSourceMetadataError,
    SourceFetchFailedError,
    hash_content,
    truncate_abstract,
)
from personal_graph_os.domain.resource import ResourceKind

_MALFORMED_SHAPE_ERRORS = (AttributeError, KeyError, TypeError, ValueError, ValidationError)

_ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
_ADAPTER_NAME = "arxiv_metadata_adapter"
_IDENTIFIER_PREFIX = "arxiv:"


def _collapse_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


class ArxivMetadataAdapter:
    """`MetadataAdapter` for `arxiv:<...>` canonical identifiers, backed by the arXiv export
    API's Atom feed."""

    def __init__(self, content_fetcher: ContentFetcher) -> None:
        self._content_fetcher = content_fetcher

    def supports(self, *, resource_kind: ResourceKind, canonical_identifier: str) -> bool:
        return canonical_identifier.startswith(_IDENTIFIER_PREFIX)

    def fetch_metadata(
        self, *, resource_kind: ResourceKind, canonical_identifier: str
    ) -> ExtractedContent:
        arxiv_id = canonical_identifier.removeprefix(_IDENTIFIER_PREFIX)
        url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
        fetched = self._content_fetcher.fetch(url)
        try:
            feed = ElementTree.fromstring(fetched.body)
        except ElementTree.ParseError as error:
            raise SourceFetchFailedError(f"{url!r} did not return a parseable Atom feed") from error

        entry = feed.find(f"{_ATOM_NAMESPACE}entry")
        if entry is None:
            raise SourceFetchFailedError(f"arXiv id {arxiv_id!r} was not found in the feed")

        try:
            title = _collapse_whitespace(entry.findtext(f"{_ATOM_NAMESPACE}title"))
            summary = _collapse_whitespace(entry.findtext(f"{_ATOM_NAMESPACE}summary"))
            published = _parse_published(entry.findtext(f"{_ATOM_NAMESPACE}published"))
            authors = tuple(
                name
                for author in entry.findall(f"{_ATOM_NAMESPACE}author")
                if (name := _collapse_whitespace(author.findtext(f"{_ATOM_NAMESPACE}name")))
                is not None
            )
            topics = tuple(
                category
                for category_element in entry.findall(
                    "{http://arxiv.org/schemas/atom}primary_category"
                )
                if (category := category_element.get("term")) is not None
            )

            evidence = ExtractionEvidence(
                kind=EvidenceKind.METADATA_LOOKUP,
                adapter_name=_ADAPTER_NAME,
                source_reference=fetched.final_url,
                content_hash=hash_content(fetched.body),
                byte_length=len(fetched.body),
            )
            return ExtractedContent(
                resource_kind=resource_kind,
                canonical_identifier=canonical_identifier,
                title=title,
                authors=authors,
                published_at=published,
                abstract=truncate_abstract(summary),
                topics=topics,
                evidence=(evidence,),
            )
        except _MALFORMED_SHAPE_ERRORS as error:
            raise MalformedSourceMetadataError(
                f"{url!r} returned an Atom feed entry in an unexpected shape: {error}"
            ) from error
