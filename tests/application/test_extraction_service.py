from __future__ import annotations

import pytest

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    UnsupportedSourceError,
    hash_content,
)
from personal_graph_os.domain.resource import ResourceKind


class _FakeMetadataAdapter:
    def __init__(self, *, prefix: str, content: ExtractedContent) -> None:
        self._prefix = prefix
        self._content = content
        self.calls: list[str] = []

    def supports(self, *, resource_kind: ResourceKind, canonical_identifier: str) -> bool:
        return canonical_identifier.startswith(self._prefix)

    def fetch_metadata(
        self, *, resource_kind: ResourceKind, canonical_identifier: str
    ) -> ExtractedContent:
        self.calls.append(canonical_identifier)
        return self._content


class _FakeContentParser:
    def __init__(self, *, content_type: str, content: ExtractedContent) -> None:
        self._content_type = content_type
        self._content = content
        self.calls: list[str] = []

    def supports(self, *, resource_kind: ResourceKind, content_type: str) -> bool:
        return content_type == self._content_type

    def parse(
        self,
        *,
        resource_kind: ResourceKind,
        canonical_identifier: str,
        fetched: FetchedResource,
    ) -> ExtractedContent:
        self.calls.append(fetched.final_url)
        return self._content


class _FakeContentFetcher:
    def __init__(self, resource: FetchedResource) -> None:
        self._resource = resource
        self.fetched_urls: list[str] = []

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        self.fetched_urls.append(url)
        return self._resource


def _content(canonical_identifier: str) -> ExtractedContent:
    return ExtractedContent(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier=canonical_identifier,
        title="Fetched Title",
        evidence=(
            ExtractionEvidence(
                kind=EvidenceKind.METADATA_LOOKUP,
                adapter_name="fake",
                source_reference=canonical_identifier,
                content_hash=hash_content("x"),
                byte_length=1,
            ),
        ),
    )


def test_extract_prefers_a_matching_metadata_adapter_over_content_fetch() -> None:
    metadata_adapter = _FakeMetadataAdapter(prefix="doi:", content=_content("doi:10.1/xyz"))
    content_fetcher = _FakeContentFetcher(
        FetchedResource(
            requested_url="https://doi.org/10.1/xyz",
            final_url="https://doi.org/10.1/xyz",
            content_type="text/html",
            body=b"<html></html>",
        )
    )
    service = ExtractionService(
        metadata_adapters=[metadata_adapter],
        content_parsers=[],
        content_fetcher=content_fetcher,
    )

    result = service.extract(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="doi:10.1/xyz",
        source_url="https://doi.org/10.1/xyz",
    )

    assert result.title == "Fetched Title"
    assert metadata_adapter.calls == ["doi:10.1/xyz"]
    assert content_fetcher.fetched_urls == []


def test_extract_falls_back_to_fetch_and_parse_when_no_metadata_adapter_matches() -> None:
    fetched_resource = FetchedResource(
        requested_url="https://example.com/article",
        final_url="https://example.com/article",
        content_type="text/html",
        body=b"<html></html>",
    )
    content_parser = _FakeContentParser(
        content_type="text/html", content=_content("https://example.com/article")
    )
    content_fetcher = _FakeContentFetcher(fetched_resource)
    service = ExtractionService(
        metadata_adapters=[],
        content_parsers=[content_parser],
        content_fetcher=content_fetcher,
    )

    result = service.extract(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/article",
        source_url="https://example.com/article",
    )

    assert result.title == "Fetched Title"
    assert content_fetcher.fetched_urls == ["https://example.com/article"]
    assert content_parser.calls == ["https://example.com/article"]


def test_extract_raises_when_no_metadata_adapter_matches_and_no_source_url_given() -> None:
    service = ExtractionService(
        metadata_adapters=[],
        content_parsers=[],
        content_fetcher=_FakeContentFetcher(
            FetchedResource(
                requested_url="x",
                final_url="x",
                content_type="text/html",
                body=b"",
            )
        ),
    )

    with pytest.raises(UnsupportedSourceError):
        service.extract(
            resource_kind=ResourceKind.OTHER,
            canonical_identifier="some:opaque:id",
            source_url=None,
        )


def test_extract_raises_when_no_content_parser_supports_the_fetched_content_type() -> None:
    content_fetcher = _FakeContentFetcher(
        FetchedResource(
            requested_url="https://example.com/video.mp4",
            final_url="https://example.com/video.mp4",
            content_type="video/mp4",
            body=b"",
        )
    )
    service = ExtractionService(
        metadata_adapters=[],
        content_parsers=[_FakeContentParser(content_type="text/html", content=_content("x"))],
        content_fetcher=content_fetcher,
    )

    with pytest.raises(UnsupportedSourceError):
        service.extract(
            resource_kind=ResourceKind.VIDEO,
            canonical_identifier="https://example.com/video.mp4",
            source_url="https://example.com/video.mp4",
        )
