"""DOI bibliographic metadata via `doi.org` CSL-JSON content negotiation (EP-2026-012 ST-03).

Separate from any generic content fetch/parse (research decision: "use ... DOI/arXiv/Crossref-
style metadata adapters; never depend on one parser for authors, publication dates, or
citations"). Recognizes only `canonical_identifier` values already resolved by
`domain.resource_identity.canonicalize_resource_identity` (a `doi:<...>` prefix), never
attempting to canonicalize a raw string itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

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

_CSL_JSON_ACCEPT = "application/vnd.citationstyles.csl+json"
_ADAPTER_NAME = "doi_metadata_adapter"
_IDENTIFIER_PREFIX = "doi:"


def _format_author(author: dict) -> str | None:
    given = (author.get("given") or "").strip()
    family = (author.get("family") or "").strip()
    literal = (author.get("literal") or "").strip()
    if given and family:
        return f"{given} {family}"
    return family or literal or None


def _parse_issued_date(csl_document: dict) -> datetime | None:
    date_parts = (csl_document.get("issued") or {}).get("date-parts") or []
    if not date_parts or not date_parts[0]:
        return None
    parts = date_parts[0]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    try:
        return datetime(int(year), int(month), int(day), tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class DoiMetadataAdapter:
    """`MetadataAdapter` for `doi:<...>` canonical identifiers, backed by `doi.org`'s
    Content Negotiation API."""

    def __init__(self, content_fetcher: ContentFetcher) -> None:
        self._content_fetcher = content_fetcher

    def supports(self, *, resource_kind: ResourceKind, canonical_identifier: str) -> bool:
        return canonical_identifier.startswith(_IDENTIFIER_PREFIX)

    def fetch_metadata(
        self, *, resource_kind: ResourceKind, canonical_identifier: str
    ) -> ExtractedContent:
        doi = canonical_identifier.removeprefix(_IDENTIFIER_PREFIX)
        url = f"https://doi.org/{doi}"
        fetched = self._content_fetcher.fetch(url, accept=_CSL_JSON_ACCEPT)
        try:
            csl_document = json.loads(fetched.body)
        except json.JSONDecodeError as error:
            raise SourceFetchFailedError(
                f"{url!r} did not return valid CSL-JSON metadata"
            ) from error

        try:
            authors = tuple(
                author
                for raw_author in csl_document.get("author", [])
                if (author := _format_author(raw_author)) is not None
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
                title=(csl_document.get("title") or "").strip() or None,
                authors=authors,
                published_at=_parse_issued_date(csl_document),
                abstract=truncate_abstract(csl_document.get("abstract")),
                evidence=(evidence,),
            )
        except _MALFORMED_SHAPE_ERRORS as error:
            raise MalformedSourceMetadataError(
                f"{url!r} returned CSL-JSON metadata in an unexpected shape: {error}"
            ) from error
