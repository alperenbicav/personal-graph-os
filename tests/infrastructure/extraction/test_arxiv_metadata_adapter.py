from __future__ import annotations

import pytest

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import SourceFetchFailedError
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.extraction.arxiv_metadata_adapter import ArxivMetadataAdapter

_ARXIV_CANONICAL_IDENTIFIER = "arxiv:2401.12345"

_ATOM_FEED_WITH_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>  A Great Paper
      About Testing  </title>
    <summary>  This paper explores testing.  </summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:primary_category term="cs.LG" />
  </entry>
</feed>
"""

_ATOM_FEED_WITHOUT_ENTRY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


class _FakeContentFetcher:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.requested_urls: list[str] = []

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        self.requested_urls.append(url)
        return FetchedResource(
            requested_url=url, final_url=url, content_type="application/atom+xml", body=self._body
        )


def test_supports_only_arxiv_prefixed_identifiers() -> None:
    adapter = ArxivMetadataAdapter(_FakeContentFetcher(_ATOM_FEED_WITH_ENTRY))
    assert adapter.supports(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
    )
    assert not adapter.supports(
        resource_kind=ResourceKind.PAPER, canonical_identifier="doi:10.1/xyz"
    )


def test_fetch_metadata_queries_the_export_api_with_the_bare_id() -> None:
    fetcher = _FakeContentFetcher(_ATOM_FEED_WITH_ENTRY)
    adapter = ArxivMetadataAdapter(fetcher)

    adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
    )

    assert fetcher.requested_urls == ["https://export.arxiv.org/api/query?id_list=2401.12345"]


def test_fetch_metadata_parses_title_summary_authors_date_and_category() -> None:
    adapter = ArxivMetadataAdapter(_FakeContentFetcher(_ATOM_FEED_WITH_ENTRY))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
    )

    assert result.title == "A Great Paper About Testing"
    assert result.abstract == "This paper explores testing."
    assert result.authors == ("Ada Lovelace", "Alan Turing")
    assert result.published_at is not None
    assert result.published_at.year == 2024
    assert result.topics == ("cs.LG",)
    assert len(result.evidence) == 1


def test_fetch_metadata_raises_when_the_id_is_not_in_the_feed() -> None:
    adapter = ArxivMetadataAdapter(_FakeContentFetcher(_ATOM_FEED_WITHOUT_ENTRY))
    with pytest.raises(SourceFetchFailedError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
        )


def test_fetch_metadata_raises_on_unparseable_xml() -> None:
    adapter = ArxivMetadataAdapter(_FakeContentFetcher(b"not xml"))
    with pytest.raises(SourceFetchFailedError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
        )


def test_fetch_metadata_truncates_a_summary_over_the_abstract_bound() -> None:
    huge_feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Title</title>
    <summary>{"z" * 25_000}</summary>
  </entry>
</feed>
""".encode()
    adapter = ArxivMetadataAdapter(_FakeContentFetcher(huge_feed))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.PAPER, canonical_identifier=_ARXIV_CANONICAL_IDENTIFIER
    )

    assert result.abstract is not None
    assert len(result.abstract) == 20_000
