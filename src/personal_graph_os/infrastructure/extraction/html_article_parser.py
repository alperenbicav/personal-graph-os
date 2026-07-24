"""Bounded HTML article parsing: title, description/abstract, and plain-text body content.

Uses only `html.parser.HTMLParser` from the standard library -- no script execution, no DOM,
and no third-party HTML dependency. `<script>`/`<style>` content is discarded entirely, and the
resulting body is truncated to `ExtractedContent.body_markdown`'s bound rather than trusting an
arbitrary page to be well-formed or finite.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    hash_content,
    truncate_abstract,
    truncate_body_markdown,
)
from personal_graph_os.domain.resource import ResourceKind

_SUPPORTED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template"})
_WHITESPACE_PATTERN = re.compile(r"[ \t\f\v]*\n[ \t\f\v]*")
_ADAPTER_NAME = "html_article_parser"


class _ArticleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.description: str | None = None
        self._text_chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            attributes = dict(attrs)
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in ("description", "og:description") and self.description is None:
                content = attributes.get("content")
                if content:
                    self.description = content.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title = (self.title or "") + data
            return
        stripped = data.strip()
        if stripped:
            self._text_chunks.append(stripped)

    @property
    def body_text(self) -> str:
        return "\n".join(self._text_chunks)


class HtmlArticleParser:
    """`ContentParser` for `text/html`/`application/xhtml+xml` article pages."""

    def supports(self, *, resource_kind: ResourceKind, content_type: str) -> bool:
        return content_type.lower() in _SUPPORTED_CONTENT_TYPES

    def parse(
        self,
        *,
        resource_kind: ResourceKind,
        canonical_identifier: str,
        fetched: FetchedResource,
    ) -> ExtractedContent:
        html_text = fetched.body.decode("utf-8", errors="replace")
        parser = _ArticleHtmlParser()
        parser.feed(html_text)
        parser.close()

        title = _WHITESPACE_PATTERN.sub("\n", parser.title).strip() if parser.title else None

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
            title=title or None,
            abstract=truncate_abstract(parser.description),
            body_markdown=truncate_body_markdown(parser.body_text),
            evidence=(evidence,),
        )
