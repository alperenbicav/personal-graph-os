"""Lightweight PDF -> text extraction for channel attachment ingress (EP-2026-012 follow-up).

Used by the Telegram bot to turn a PDF attachment into analyzable text without the optional,
heavy Docling stack: `pypdf` is a small pure-Python dependency already in the project. This is
intentionally a *content* extractor for analysis, not a bibliographic metadata extractor --
authors/dates for PDFs that need them are handled elsewhere.
"""

from __future__ import annotations

import io

_MAX_PDF_TEXT_CHARS = 40000
_PAGE_SEPARATOR = "\n\n"


def extract_pdf_text(pdf_bytes: bytes, *, max_chars: int = _MAX_PDF_TEXT_CHARS) -> str:
    """Return the concatenated text of `pdf_bytes`, bounded to `max_chars`. Empty for a file
    whose text extraction yields nothing or that cannot be parsed."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:  # noqa: BLE001 - an unreadable file must not break the channel
        return ""
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - a malformed page must not break the whole file
            text = ""
        if text:
            pages.append(text)
    joined = _PAGE_SEPARATOR.join(pages).strip()
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars] + "\n…[truncated]"
