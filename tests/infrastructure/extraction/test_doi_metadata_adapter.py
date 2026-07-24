from __future__ import annotations

import json

import pytest

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import (
    MalformedSourceMetadataError,
    SourceFetchFailedError,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.extraction.doi_metadata_adapter import DoiMetadataAdapter

_DOI_CANONICAL_IDENTIFIER = "doi:10.1000/xyz123"


class _FakeContentFetcher:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.requested_urls: list[str] = []
        self.requested_accepts: list[str | None] = []

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        self.requested_urls.append(url)
        self.requested_accepts.append(accept)
        return FetchedResource(
            requested_url=url, final_url=url, content_type="application/json", body=self._body
        )


def _csl_json(**overrides: object) -> bytes:
    document: dict[str, object] = {
        "title": "A Paper About Testing",
        "author": [{"given": "Ada", "family": "Lovelace"}, {"literal": "Some Committee"}],
        "issued": {"date-parts": [[2024, 3, 15]]},
        "abstract": "An abstract about testing.",
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def test_supports_only_doi_prefixed_identifiers() -> None:
    adapter = DoiMetadataAdapter(_FakeContentFetcher(b"{}"))
    assert adapter.supports(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
    )
    assert not adapter.supports(
        resource_kind=ResourceKind.PAPER, canonical_identifier="arxiv:1234.5678"
    )


def test_fetch_metadata_requests_csl_json_via_content_negotiation() -> None:
    fetcher = _FakeContentFetcher(_csl_json())
    adapter = DoiMetadataAdapter(fetcher)

    adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
    )

    assert fetcher.requested_urls == ["https://doi.org/10.1000/xyz123"]
    assert fetcher.requested_accepts == ["application/vnd.citationstyles.csl+json"]


def test_fetch_metadata_parses_title_authors_date_and_abstract() -> None:
    adapter = DoiMetadataAdapter(_FakeContentFetcher(_csl_json()))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
    )

    assert result.title == "A Paper About Testing"
    assert result.authors == ("Ada Lovelace", "Some Committee")
    assert result.published_at is not None
    assert result.published_at.year == 2024
    assert result.published_at.month == 3
    assert result.published_at.day == 15
    assert result.abstract == "An abstract about testing."
    assert len(result.evidence) == 1


def test_fetch_metadata_tolerates_a_missing_issued_date() -> None:
    adapter = DoiMetadataAdapter(_FakeContentFetcher(_csl_json(issued={})))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
    )

    assert result.published_at is None


def test_fetch_metadata_raises_on_invalid_json() -> None:
    adapter = DoiMetadataAdapter(_FakeContentFetcher(b"not json"))
    with pytest.raises(SourceFetchFailedError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
        )


def test_fetch_metadata_truncates_an_abstract_over_the_bound() -> None:
    adapter = DoiMetadataAdapter(_FakeContentFetcher(_csl_json(abstract="z" * 25_000)))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
    )

    assert result.abstract is not None
    assert len(result.abstract) == 20_000


def test_fetch_metadata_raises_a_typed_error_on_a_well_formed_but_unexpected_shape() -> None:
    # Valid JSON, but "author" is a string instead of a list -- iterating it yields
    # characters, and `_format_author` calling `.get()` on each one raises `AttributeError`.
    adapter = DoiMetadataAdapter(_FakeContentFetcher(_csl_json(author="not-a-list")))

    with pytest.raises(MalformedSourceMetadataError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.PAPER, canonical_identifier=_DOI_CANONICAL_IDENTIFIER
        )
