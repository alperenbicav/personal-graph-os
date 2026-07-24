"""PDF parsing behind the `ContentParser` protocol, backed by Docling (EP-2026-012 ST-03,
research decision: "use a replaceable parser adapter ... never depend on one parser for
authors, publication dates, or citations").

Docling is deliberately not a hard dependency: it is a large, optional local ML stack, and
bibliographic metadata extraction is explicitly not something it promises yet (see
`RESEARCH.md`). `DoclingPdfParser` imports it lazily on first use and raises
`MissingOptionalDependencyError` with an actionable message when it is absent, so a workspace
that never captures PDFs pays no cost and one that does gets a clear, visible failure instead of
a confusing `ImportError` deep in a call stack.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Protocol

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import (
    ContentParsingFailedError,
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    MissingOptionalDependencyError,
    hash_content,
    truncate_body_markdown,
)
from personal_graph_os.domain.resource import ResourceKind

_SUPPORTED_CONTENT_TYPES = frozenset({"application/pdf"})
_ADAPTER_NAME = "docling_pdf_parser"


class PdfMarkdownConverter(Protocol):
    """The narrow slice of Docling's document-conversion surface this parser needs, isolated
    behind a protocol so a fake can stand in for tests and a future non-Docling converter can
    be swapped in without changing `DoclingPdfParser`."""

    def convert_to_markdown(self, pdf_bytes: bytes) -> str:
        """Convert `pdf_bytes` to a Markdown rendering of its text content."""
        ...


def _load_docling_converter() -> PdfMarkdownConverter:
    try:
        from docling.datamodel.base_models import DocumentStream  # type: ignore
        from docling.document_converter import DocumentConverter  # type: ignore
    except ImportError as error:
        raise MissingOptionalDependencyError(
            "PDF extraction requires the optional 'docling' dependency, which is not "
            "installed in this environment; install it (e.g. `uv add docling`) to parse PDFs"
        ) from error

    class _DoclingConverter:
        def __init__(self) -> None:
            self._converter = DocumentConverter()

        def convert_to_markdown(self, pdf_bytes: bytes) -> str:
            stream = DocumentStream(name="source.pdf", stream=io.BytesIO(pdf_bytes))
            result = self._converter.convert(stream)
            return result.document.export_to_markdown()

    return _DoclingConverter()


class DoclingPdfParser:
    """`ContentParser` for `application/pdf`, using an injectable `PdfMarkdownConverter` so
    tests never require the real Docling dependency."""

    def __init__(self, converter_factory: Callable[[], PdfMarkdownConverter] | None = None) -> None:
        self._converter: PdfMarkdownConverter | None = None
        self._converter_factory = converter_factory

    def supports(self, *, resource_kind: ResourceKind, content_type: str) -> bool:
        return content_type.lower() in _SUPPORTED_CONTENT_TYPES

    def parse(
        self,
        *,
        resource_kind: ResourceKind,
        canonical_identifier: str,
        fetched: FetchedResource,
    ) -> ExtractedContent:
        try:
            converter = self._require_converter()
            markdown = converter.convert_to_markdown(fetched.body)
        except MissingOptionalDependencyError:
            raise
        except Exception as error:
            raise ContentParsingFailedError(
                f"failed to convert PDF content from {fetched.final_url!r}: {error}"
            ) from error

        evidence = ExtractionEvidence(
            kind=EvidenceKind.FETCHED_CONTENT,
            adapter_name=_ADAPTER_NAME,
            source_reference=fetched.final_url,
            content_hash=hash_content(fetched.body),
            byte_length=len(fetched.body),
        )
        return ExtractedContent(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            body_markdown=truncate_body_markdown(markdown),
            evidence=(evidence,),
        )

    def _require_converter(self) -> PdfMarkdownConverter:
        if self._converter is None:
            self._converter = (
                self._converter_factory() if self._converter_factory else _load_docling_converter()
            )
        return self._converter
