from __future__ import annotations

import pytest

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import (
    ContentParsingFailedError,
    MissingOptionalDependencyError,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.extraction.docling_pdf_parser import DoclingPdfParser


def _fetched() -> FetchedResource:
    return FetchedResource(
        requested_url="https://example.com/paper.pdf",
        final_url="https://example.com/paper.pdf",
        content_type="application/pdf",
        body=b"%PDF-1.4 fake bytes",
    )


def test_supports_only_pdf_content_type() -> None:
    parser = DoclingPdfParser(converter_factory=lambda: _FakeConverter("irrelevant"))
    assert parser.supports(resource_kind=ResourceKind.PAPER, content_type="application/pdf")
    assert not parser.supports(resource_kind=ResourceKind.PAPER, content_type="text/html")


def test_parse_without_docling_installed_raises_a_clear_error() -> None:
    # Docling is intentionally not a hard dependency (RESEARCH.md); this environment does not
    # have it installed, so the default factory's lazy import genuinely fails here.
    parser = DoclingPdfParser()
    with pytest.raises(MissingOptionalDependencyError):
        parser.parse(
            resource_kind=ResourceKind.PAPER,
            canonical_identifier="https://example.com/paper.pdf",
            fetched=_fetched(),
        )


class _FakeConverter:
    def __init__(self, markdown: str) -> None:
        self._markdown = markdown

    def convert_to_markdown(self, pdf_bytes: bytes) -> str:
        return self._markdown


def test_parse_with_an_injected_converter_returns_bounded_evidence() -> None:
    parser = DoclingPdfParser(converter_factory=lambda: _FakeConverter("# Title\n\nBody text."))
    result = parser.parse(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="https://example.com/paper.pdf",
        fetched=_fetched(),
    )

    assert result.body_markdown == "# Title\n\nBody text."
    assert len(result.evidence) == 1
    assert result.evidence[0].adapter_name == "docling_pdf_parser"


def test_parse_truncates_markdown_over_the_bound() -> None:
    huge_markdown = "x" * 250_000
    parser = DoclingPdfParser(converter_factory=lambda: _FakeConverter(huge_markdown))
    result = parser.parse(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="https://example.com/paper.pdf",
        fetched=_fetched(),
    )

    assert result.body_markdown is not None
    assert len(result.body_markdown) == 200_000


class _BrokenConverter:
    def convert_to_markdown(self, pdf_bytes: bytes) -> str:
        raise RuntimeError("docling internal failure: corrupt PDF structure")


def test_parse_translates_a_converter_failure_into_a_typed_extraction_error() -> None:
    parser = DoclingPdfParser(converter_factory=_BrokenConverter)
    with pytest.raises(ContentParsingFailedError):
        parser.parse(
            resource_kind=ResourceKind.PAPER,
            canonical_identifier="https://example.com/paper.pdf",
            fetched=_fetched(),
        )


def _raise_construction_error() -> _FakeConverter:
    raise RuntimeError("docling model/runtime initialization failed")


def test_parse_translates_a_converter_construction_failure_into_a_typed_extraction_error() -> None:
    # Delta review finding: real Docling initialization can fail for configuration/model/
    # runtime reasons before `convert_to_markdown` is ever called -- the factory call itself
    # must be inside the same typed-error boundary, not just the conversion call.
    parser = DoclingPdfParser(converter_factory=_raise_construction_error)
    with pytest.raises(ContentParsingFailedError):
        parser.parse(
            resource_kind=ResourceKind.PAPER,
            canonical_identifier="https://example.com/paper.pdf",
            fetched=_fetched(),
        )


def test_converter_is_created_once_and_reused_across_parses() -> None:
    creation_count = 0

    def factory() -> _FakeConverter:
        nonlocal creation_count
        creation_count += 1
        return _FakeConverter("content")

    parser = DoclingPdfParser(converter_factory=factory)
    parser.parse(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="a",
        fetched=_fetched(),
    )
    parser.parse(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="b",
        fetched=_fetched(),
    )

    assert creation_count == 1
