"""PDF-text extraction boundary for channel ingress (EP-2026-012 follow-up).

`application.telegram_service` needs to turn a PDF attachment into analyzable text but must not
depend on a concrete extractor (pypdf lives in `infrastructure`). This protocol keeps that
dependency injected at the composition root, mirroring the `GraphAgent`/`AgentChatProvider`
adapter pattern.
"""

from __future__ import annotations

from typing import Protocol


class PdfTextExtractor(Protocol):
    """Extracts the text of a PDF's bytes (bounded); returns "" when nothing is readable."""

    def __call__(self, pdf_bytes: bytes) -> str: ...


__all__ = ["PdfTextExtractor"]
