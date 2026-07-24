from __future__ import annotations

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.extraction.html_article_parser import HtmlArticleParser

_PAGE = b"""
<html>
<head>
  <title>  My Article Title  </title>
  <meta name="description" content="A short description.">
  <script>window.evil = true;</script>
  <style>.hidden { display: none; }</style>
</head>
<body>
  <h1>My Article Title</h1>
  <p>First paragraph of the body.</p>
  <p>Second paragraph.</p>
</body>
</html>
"""


def _fetched(body: bytes = _PAGE, content_type: str = "text/html") -> FetchedResource:
    return FetchedResource(
        requested_url="https://example.com/article",
        final_url="https://example.com/article",
        content_type=content_type,
        body=body,
    )


def test_supports_html_content_types() -> None:
    parser = HtmlArticleParser()
    assert parser.supports(resource_kind=ResourceKind.ARTICLE, content_type="text/html")
    assert parser.supports(resource_kind=ResourceKind.ARTICLE, content_type="application/xhtml+xml")
    assert not parser.supports(resource_kind=ResourceKind.ARTICLE, content_type="application/pdf")


def test_parse_extracts_title_description_and_body_text() -> None:
    parser = HtmlArticleParser()
    result = parser.parse(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/article",
        fetched=_fetched(),
    )

    assert result.title == "My Article Title"
    assert result.abstract == "A short description."
    assert result.body_markdown is not None
    assert "First paragraph of the body." in result.body_markdown
    assert "Second paragraph." in result.body_markdown
    assert "evil" not in result.body_markdown
    assert "display: none" not in result.body_markdown
    assert len(result.evidence) == 1
    assert result.evidence[0].byte_length == len(_PAGE)


def test_parse_handles_a_page_with_no_title_or_description() -> None:
    parser = HtmlArticleParser()
    result = parser.parse(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/bare",
        fetched=_fetched(body=b"<html><body><p>Just text.</p></body></html>"),
    )

    assert result.title is None
    assert result.abstract is None
    assert result.body_markdown == "Just text."


def test_parse_truncates_a_body_over_the_bound_instead_of_raising() -> None:
    huge_paragraph = "x" * 250_000
    page = f"<html><body><p>{huge_paragraph}</p></body></html>".encode()
    parser = HtmlArticleParser()

    result = parser.parse(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/huge",
        fetched=_fetched(body=page),
    )

    assert result.body_markdown is not None
    assert len(result.body_markdown) == 200_000


def test_parse_truncates_a_description_over_the_abstract_bound() -> None:
    huge_description = "y" * 25_000
    page = f'<html><head><meta name="description" content="{huge_description}"></head>'
    page += "<body><p>text</p></body></html>"
    parser = HtmlArticleParser()

    result = parser.parse(
        resource_kind=ResourceKind.ARTICLE,
        canonical_identifier="https://example.com/huge-description",
        fetched=_fetched(body=page.encode()),
    )

    assert result.abstract is not None
    assert len(result.abstract) == 20_000
