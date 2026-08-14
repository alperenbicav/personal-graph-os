"""Unit tests for the lightweight pypdf text extractor."""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from personal_graph_os.infrastructure.extraction.pdf_text import extract_pdf_text


def _text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = StreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_extracts_text_from_a_single_page_pdf() -> None:
    pdf = _text_pdf("Hello PDF World")
    text = extract_pdf_text(pdf)
    assert "Hello PDF World" in text


def test_empty_for_garbage_bytes() -> None:
    assert extract_pdf_text(b"not a pdf at all") == ""


def test_bounds_very_long_output() -> None:
    pdf = _text_pdf("A" * 3000)
    text = extract_pdf_text(pdf, max_chars=100)
    assert len(text) <= 100 + len("\n…[truncated]")
    assert "[truncated]" in text
