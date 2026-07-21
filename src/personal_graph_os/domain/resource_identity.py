"""Pure, offline canonical-identity resolution for a research resource's raw URL/identifier.

Decision #5 (`WORK.md`): identity is resolved DOI, then arXiv, then GitHub repository, then a
normalized generic HTTP(S) URL — deterministically and without any network call, so the same
input always canonicalizes to the same `canonical_identifier` regardless of when or how many
times it is imported.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.resource import ResourceKind

_DOI_URL_PATTERN = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)$", re.IGNORECASE)
_BARE_DOI_PATTERN = re.compile(r"^(?:doi:)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)

_ARXIV_URL_PATTERN = re.compile(
    r"^https?://arxiv\.org/abs/([a-z\-]*/?\d{4,7}(?:\.\d{4,5})?)(v\d+)?/?$", re.IGNORECASE
)
_BARE_ARXIV_PATTERN = re.compile(r"^arxiv:([a-z\-]*/?\d{4,7}(?:\.\d{4,5})?)(v\d+)?$", re.IGNORECASE)

_GITHUB_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/\s#?]+)/([^/\s#?]+)(?:[/#?].*)?$", re.IGNORECASE
)
_BARE_GITHUB_PATTERN = re.compile(r"^github:([^/\s]+)/([^/\s]+)$", re.IGNORECASE)

_TRACKING_QUERY_PARAM_PREFIXES = ("utm_",)
_TRACKING_QUERY_PARAM_NAMES = frozenset({"gclid", "fbclid", "ref", "mc_cid", "mc_eid"})


class ResourceIdentity(BaseModel):
    """The result of canonicalizing one raw URL/identifier string."""

    canonical_identifier: str
    normalized_source_url: str | None
    detected_kind: ResourceKind | None


def _strip_doi(raw: str) -> str | None:
    match = _DOI_URL_PATTERN.match(raw) or _BARE_DOI_PATTERN.match(raw)
    return match.group(1) if match else None


def _strip_arxiv(raw: str) -> str | None:
    match = _ARXIV_URL_PATTERN.match(raw) or _BARE_ARXIV_PATTERN.match(raw)
    return match.group(1) if match else None


def _strip_github(raw: str) -> tuple[str, str] | None:
    match = _GITHUB_URL_PATTERN.match(raw) or _BARE_GITHUB_PATTERN.match(raw)
    if match is None:
        return None
    owner, repository = match.group(1), match.group(2)
    if repository.lower().endswith(".git"):
        repository = repository[: -len(".git")]
    return owner, repository


def _is_tracking_query_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_QUERY_PARAM_NAMES or lowered.startswith(
        _TRACKING_QUERY_PARAM_PREFIXES
    )


def _normalize_generic_url(raw: str) -> str | None:
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None

    host = parsed.hostname or ""
    default_port = {"http": 80, "https": 443}[parsed.scheme.lower()]
    netloc = host.lower()
    if parsed.port is not None and parsed.port != default_port:
        netloc = f"{netloc}:{parsed.port}"

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    remaining_query = sorted(
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_param(name)
    )

    return urlunparse((parsed.scheme.lower(), netloc, path, "", urlencode(remaining_query), ""))


def canonicalize_resource_identity(raw: str) -> ResourceIdentity:
    """Resolve `raw` (a URL or a bare identifier) to a stable canonical identity.

    Raises `InvariantViolationError` when `raw` is empty or is neither a recognized DOI/arXiv/
    GitHub identifier nor an absolute http(s) URL — there is nothing offline-deterministic left
    to canonicalize it to.
    """
    stripped = raw.strip()
    if not stripped:
        raise InvariantViolationError("resource identity input must not be empty")

    doi = _strip_doi(stripped)
    if doi is not None:
        normalized_doi = doi.lower()
        return ResourceIdentity(
            canonical_identifier=f"doi:{normalized_doi}",
            normalized_source_url=f"https://doi.org/{normalized_doi}",
            detected_kind=ResourceKind.PAPER,
        )

    arxiv_id = _strip_arxiv(stripped)
    if arxiv_id is not None:
        normalized_arxiv_id = arxiv_id.lower()
        return ResourceIdentity(
            canonical_identifier=f"arxiv:{normalized_arxiv_id}",
            normalized_source_url=f"https://arxiv.org/abs/{normalized_arxiv_id}",
            detected_kind=ResourceKind.PAPER,
        )

    github = _strip_github(stripped)
    if github is not None:
        owner, repository = github
        normalized_owner, normalized_repository = owner.lower(), repository.lower()
        return ResourceIdentity(
            canonical_identifier=f"github:{normalized_owner}/{normalized_repository}",
            normalized_source_url=f"https://github.com/{normalized_owner}/{normalized_repository}",
            detected_kind=ResourceKind.GITHUB_REPOSITORY,
        )

    normalized_url = _normalize_generic_url(stripped)
    if normalized_url is not None:
        return ResourceIdentity(
            canonical_identifier=normalized_url,
            normalized_source_url=normalized_url,
            detected_kind=None,
        )

    raise InvariantViolationError(f"could not canonicalize resource identity from {raw!r}")
