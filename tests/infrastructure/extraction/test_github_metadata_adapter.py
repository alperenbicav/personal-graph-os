from __future__ import annotations

import json

import pytest

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import (
    MalformedSourceMetadataError,
    SourceAccessDeniedError,
    SourceFetchFailedError,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.extraction.github_metadata_adapter import (
    GitHubMetadataAdapter,
)

_GITHUB_CANONICAL_IDENTIFIER = "github:octocat/hello-world"


class _FakeContentFetcher:
    def __init__(self, *, body: bytes | None = None, error: Exception | None = None) -> None:
        self._body = body
        self._error = error
        self.requested_urls: list[str] = []
        self.requested_accepts: list[str | None] = []

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        self.requested_urls.append(url)
        self.requested_accepts.append(accept)
        if self._error is not None:
            raise self._error
        assert self._body is not None
        return FetchedResource(
            requested_url=url, final_url=url, content_type="application/json", body=self._body
        )


def _repository_json(**overrides: object) -> bytes:
    document: dict[str, object] = {
        "full_name": "octocat/hello-world",
        "name": "hello-world",
        "description": "My first repository.",
        "license": {"name": "MIT License"},
        "language": "Python",
        "stargazers_count": 42,
        "topics": ["example", "octocat"],
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def test_supports_only_github_prefixed_identifiers() -> None:
    adapter = GitHubMetadataAdapter(_FakeContentFetcher(body=_repository_json()))
    assert adapter.supports(
        resource_kind=ResourceKind.GITHUB_REPOSITORY,
        canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
    )
    assert not adapter.supports(
        resource_kind=ResourceKind.GITHUB_REPOSITORY, canonical_identifier="doi:10.1/xyz"
    )


def test_fetch_metadata_requests_the_repos_endpoint_with_the_github_accept_header() -> None:
    fetcher = _FakeContentFetcher(body=_repository_json())
    adapter = GitHubMetadataAdapter(fetcher)

    adapter.fetch_metadata(
        resource_kind=ResourceKind.GITHUB_REPOSITORY,
        canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
    )

    assert fetcher.requested_urls == ["https://api.github.com/repos/octocat/hello-world"]
    assert fetcher.requested_accepts == ["application/vnd.github+json"]


def test_fetch_metadata_parses_repository_fields() -> None:
    adapter = GitHubMetadataAdapter(_FakeContentFetcher(body=_repository_json()))

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.GITHUB_REPOSITORY,
        canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
    )

    assert result.title == "octocat/hello-world"
    assert result.abstract == "My first repository."
    assert result.license_name == "MIT License"
    assert result.primary_language == "Python"
    assert result.star_count == 42
    assert result.topics == ("example", "octocat")
    assert len(result.evidence) == 1


def test_fetch_metadata_raises_access_denied_when_the_fetcher_reports_404() -> None:
    adapter = GitHubMetadataAdapter(
        _FakeContentFetcher(error=SourceFetchFailedError("not found", status_code=404))
    )
    with pytest.raises(SourceAccessDeniedError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.GITHUB_REPOSITORY,
            canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
        )


def test_fetch_metadata_propagates_other_fetch_failures() -> None:
    adapter = GitHubMetadataAdapter(
        _FakeContentFetcher(error=SourceFetchFailedError("server error", status_code=500))
    )
    with pytest.raises(SourceFetchFailedError) as excinfo:
        adapter.fetch_metadata(
            resource_kind=ResourceKind.GITHUB_REPOSITORY,
            canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
        )
    assert excinfo.value.status_code == 500


def test_fetch_metadata_raises_on_invalid_json() -> None:
    adapter = GitHubMetadataAdapter(_FakeContentFetcher(body=b"not json"))
    with pytest.raises(SourceFetchFailedError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.GITHUB_REPOSITORY,
            canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
        )


def test_fetch_metadata_truncates_a_description_over_the_abstract_bound() -> None:
    adapter = GitHubMetadataAdapter(
        _FakeContentFetcher(body=_repository_json(description="z" * 25_000))
    )

    result = adapter.fetch_metadata(
        resource_kind=ResourceKind.GITHUB_REPOSITORY,
        canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
    )

    assert result.abstract is not None
    assert len(result.abstract) == 20_000


def test_fetch_metadata_raises_a_typed_error_on_a_well_formed_but_unexpected_shape() -> None:
    # Valid JSON, but "license" is a string instead of an object -- `.get("name")` on it raises.
    adapter = GitHubMetadataAdapter(_FakeContentFetcher(body=_repository_json(license="MIT")))

    with pytest.raises(MalformedSourceMetadataError):
        adapter.fetch_metadata(
            resource_kind=ResourceKind.GITHUB_REPOSITORY,
            canonical_identifier=_GITHUB_CANONICAL_IDENTIFIER,
        )
