"""GitHub repository metadata via the REST API (EP-2026-012 ST-03).

Recognizes only `canonical_identifier` values already resolved by
`domain.resource_identity.canonicalize_resource_identity` (a `github:<owner>/<repository>`
prefix). A private repository the caller has no token for returns HTTP 404 from GitHub's API
indistinguishably from a nonexistent one; both surface here as `SourceAccessDeniedError` so a
private/unsupported source fails visibly rather than silently returning empty metadata.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from personal_graph_os.application.extraction_adapters import ContentFetcher
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    MalformedSourceMetadataError,
    SourceAccessDeniedError,
    SourceFetchFailedError,
    hash_content,
    truncate_abstract,
)
from personal_graph_os.domain.resource import ResourceKind

_MALFORMED_SHAPE_ERRORS = (AttributeError, KeyError, TypeError, ValueError, ValidationError)

_GITHUB_API_ACCEPT = "application/vnd.github+json"
_ADAPTER_NAME = "github_metadata_adapter"
_IDENTIFIER_PREFIX = "github:"


class GitHubMetadataAdapter:
    """`MetadataAdapter` for `github:<owner>/<repository>` canonical identifiers, backed by
    the GitHub REST API's repository endpoint."""

    def __init__(self, content_fetcher: ContentFetcher) -> None:
        self._content_fetcher = content_fetcher

    def supports(self, *, resource_kind: ResourceKind, canonical_identifier: str) -> bool:
        return canonical_identifier.startswith(_IDENTIFIER_PREFIX)

    def fetch_metadata(
        self, *, resource_kind: ResourceKind, canonical_identifier: str
    ) -> ExtractedContent:
        owner_and_repository = canonical_identifier.removeprefix(_IDENTIFIER_PREFIX)
        url = f"https://api.github.com/repos/{owner_and_repository}"
        try:
            fetched = self._content_fetcher.fetch(url, accept=_GITHUB_API_ACCEPT)
        except SourceFetchFailedError as error:
            if error.status_code == 404:
                raise SourceAccessDeniedError(
                    f"github repository {owner_and_repository!r} is private or does not exist"
                ) from error
            raise

        try:
            repository = json.loads(fetched.body)
        except json.JSONDecodeError as error:
            raise SourceFetchFailedError(
                f"{url!r} did not return valid GitHub repository JSON"
            ) from error

        try:
            license_info = repository.get("license") or {}
            topics = tuple(repository.get("topics") or ())

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
                title=repository.get("full_name") or repository.get("name") or None,
                abstract=truncate_abstract(repository.get("description")),
                license_name=(license_info.get("name") or "").strip() or None,
                primary_language=(repository.get("language") or "").strip() or None,
                star_count=repository.get("stargazers_count"),
                topics=topics,
                evidence=(evidence,),
            )
        except _MALFORMED_SHAPE_ERRORS as error:
            raise MalformedSourceMetadataError(
                f"{url!r} returned repository metadata in an unexpected shape: {error}"
            ) from error
