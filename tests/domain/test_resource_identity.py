from __future__ import annotations

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.resource_identity import canonicalize_resource_identity

_DOI_CASES = [
    ("https://doi.org/10.1000/xyz123", "doi:10.1000/xyz123"),
    ("https://dx.doi.org/10.1000/XYZ123", "doi:10.1000/xyz123"),
    ("doi:10.1000/xyz123", "doi:10.1000/xyz123"),
    ("10.1000/xyz123", "doi:10.1000/xyz123"),
]


@pytest.mark.parametrize("raw,expected_identifier", _DOI_CASES)
def test_doi_variants_canonicalize_to_the_same_identity(raw: str, expected_identifier: str) -> None:
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == expected_identifier
    assert identity.detected_kind is ResourceKind.PAPER
    assert identity.normalized_source_url == f"https://doi.org/{expected_identifier[4:]}"


_DOI_TRACKING_CASES = [
    "https://doi.org/10.1000/xyz123?utm_source=one",
    "https://doi.org/10.1000/xyz123?utm_source=two",
    "https://doi.org/10.1000/xyz123#section-2",
    "https://dx.doi.org/10.1000/XYZ123?gclid=abc",
]


@pytest.mark.parametrize("raw", _DOI_TRACKING_CASES)
def test_doi_query_and_fragment_are_excluded_from_the_canonical_identifier(raw: str) -> None:
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == "doi:10.1000/xyz123"


_ARXIV_CASES = [
    ("https://arxiv.org/abs/2401.00001", "arxiv:2401.00001"),
    ("https://arxiv.org/abs/2401.00001v1", "arxiv:2401.00001"),
    ("https://arxiv.org/abs/2401.00001v2", "arxiv:2401.00001"),
    ("arxiv:2401.00001v3", "arxiv:2401.00001"),
]


@pytest.mark.parametrize("raw,expected_identifier", _ARXIV_CASES)
def test_arxiv_versions_canonicalize_to_the_same_identity(
    raw: str, expected_identifier: str
) -> None:
    """Different versions of the same paper (v1/v2/v3) must resolve to one canonical id."""
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == expected_identifier
    assert identity.detected_kind is ResourceKind.PAPER


_GITHUB_CASES = [
    ("https://github.com/octocat/Hello-World", "github:octocat/hello-world"),
    ("https://github.com/octocat/Hello-World.git", "github:octocat/hello-world"),
    ("https://github.com/octocat/Hello-World/issues/42", "github:octocat/hello-world"),
    ("https://www.github.com/OCTOCAT/HELLO-WORLD", "github:octocat/hello-world"),
    ("github:octocat/Hello-World", "github:octocat/hello-world"),
]


@pytest.mark.parametrize("raw,expected_identifier", _GITHUB_CASES)
def test_github_repository_urls_canonicalize_to_owner_repo(
    raw: str, expected_identifier: str
) -> None:
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == expected_identifier
    assert identity.detected_kind is ResourceKind.GITHUB_REPOSITORY
    assert identity.normalized_source_url == "https://github.com/octocat/hello-world"


def test_generic_https_url_is_normalized_and_has_no_detected_kind() -> None:
    identity = canonicalize_resource_identity("https://Example.com/Path/")
    assert identity.canonical_identifier == "https://example.com/Path"
    assert identity.detected_kind is None


def test_generic_url_strips_fragment_and_tracking_query_params() -> None:
    raw = "https://example.com/article?utm_source=newsletter&id=7&gclid=abc#section-2"
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == "https://example.com/article?id=7"


def test_generic_url_removes_default_port_but_keeps_a_custom_one() -> None:
    assert canonicalize_resource_identity("https://example.com:443/x").canonical_identifier == (
        "https://example.com/x"
    )
    assert canonicalize_resource_identity("http://example.com:8080/x").canonical_identifier == (
        "http://example.com:8080/x"
    )


def test_canonicalization_is_idempotent_for_every_kind() -> None:
    for raw in (
        "https://doi.org/10.1000/xyz123",
        "https://arxiv.org/abs/2401.00001v2",
        "https://github.com/octocat/Hello-World",
        "https://example.com/a/b?z=1&a=2",
        "https://[2001:db8::1]:8443/paper",
    ):
        once = canonicalize_resource_identity(raw)
        twice = canonicalize_resource_identity(once.canonical_identifier)
        assert twice.canonical_identifier == once.canonical_identifier


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a url",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "https://example.com:bad/path",
        "https://[bad",
        "https://example.com:99999/path",
        "https://[::1]:99999/path",
    ],
)
def test_invalid_or_unsupported_input_is_rejected(raw: str) -> None:
    with pytest.raises(InvariantViolationError):
        canonicalize_resource_identity(raw)


@pytest.mark.parametrize(
    "raw,expected_identifier",
    [
        ("https://[::1]/path", "https://[::1]/path"),
        ("https://[::1]:443/path", "https://[::1]/path"),
        ("https://[::1]:8443/path", "https://[::1]:8443/path"),
        ("https://[2001:DB8::1]/paper", "https://[2001:db8::1]/paper"),
    ],
)
def test_ipv6_urls_preserve_brackets_and_canonicalize(raw: str, expected_identifier: str) -> None:
    identity = canonicalize_resource_identity(raw)
    assert identity.canonical_identifier == expected_identifier
    # The result must itself be a valid, re-canonicalizable URL — not merely accepted once.
    replayed = canonicalize_resource_identity(identity.canonical_identifier)
    assert replayed.canonical_identifier == expected_identifier
