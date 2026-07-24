"""Bounded, evidence-carrying structured extraction from a captured source (EP-2026-012 ST-03).

Fetch/parse/metadata adapters (`application/extraction_adapters.py` and their concrete
`infrastructure/extraction` implementations) turn a resource's canonical identifier and source
URL into one `ExtractedContent` -- bounded structured fields plus content-hashed,
provenance-stamped `ExtractionEvidence` -- without ever performing a canonical write themselves.
A source no registered adapter supports, or one that requires authentication/authorization this
workspace does not have, fails visibly by raising an `ExtractionError`; no partial or
best-effort content is ever returned in its place.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import DomainError, InvariantViolationError
from personal_graph_os.domain.resource import ResourceKind

# Extracted body text is evidence for a later human/agent to read, never executable
# instructions and never unbounded -- a large page/PDF is truncated rather than stored whole.
# Exported (review finding S3-R03) so every adapter truncates against the same one bound
# instead of each parser/metadata adapter risking its own, possibly drifting, local limit.
MAX_BODY_MARKDOWN_LENGTH = 200_000
MAX_ABSTRACT_LENGTH = 20_000
_CONTENT_HASH_LENGTH = 64
_CONTENT_HASH_ALPHABET = frozenset("0123456789abcdef")


class ExtractionError(DomainError):
    """Base error for a source this layer could not turn into extracted evidence."""


class UnsupportedSourceError(ExtractionError):
    """Raised when no registered metadata/content adapter can handle the given resource kind,
    canonical identifier, or fetched content type."""


class SourceAccessDeniedError(ExtractionError):
    """Raised when a source exists but requires authentication/authorization this workspace
    does not have -- a private repository, a paywalled article, a login-gated page."""


class SourceFetchFailedError(ExtractionError):
    """Raised when a source could not be retrieved for a reason other than access control --
    a timeout, a network error, or an unexpected non-2xx response.

    Carries the response's `status_code` when the failure came from an HTTP response rather
    than a transport-level error, so a caller can react to a specific status (e.g. a metadata
    adapter distinguishing a 404 "private or missing" repository) without parsing the message.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MissingOptionalDependencyError(ExtractionError):
    """Raised when a parser adapter needs an optional dependency (e.g. Docling) that is not
    installed in this environment."""


class ContentParsingFailedError(ExtractionError):
    """Raised when a fetched resource was retrieved successfully but a content parser (e.g.
    the Docling PDF converter) could not turn its bytes into structured content.

    Every parser-specific/third-party exception must be caught and re-raised as this type
    (review finding S3-R04) so a caller only ever sees `ExtractionError` from this layer."""


class MalformedSourceMetadataError(ExtractionError):
    """Raised when a metadata adapter's upstream response was syntactically valid (parseable
    JSON/XML) but had an unexpected shape -- a missing/mistyped field a well-formed response
    always has -- so building `ExtractedContent` from it failed.

    Every such shape mismatch must be caught and re-raised as this type (review finding
    S3-R04) rather than leaking a raw `KeyError`/`AttributeError`/`ValidationError`."""


def hash_content(content: bytes | str) -> str:
    """The stable SHA-256 hex digest of exactly what an adapter read, never the bytes
    themselves -- evidence proves what was read without duplicating a copy of the source."""
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(payload).hexdigest()


def truncate_body_markdown(text: str | None) -> str | None:
    """Bound and normalize a parser's raw body text to `MAX_BODY_MARKDOWN_LENGTH` before it
    ever reaches `ExtractedContent` construction (review finding S3-R03): the model's own
    validator remains a defense-in-depth invariant, not the mechanism a well-behaved adapter
    relies on to stay within bounds."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped[:MAX_BODY_MARKDOWN_LENGTH] or None


def truncate_abstract(text: str | None) -> str | None:
    """Bound and normalize a metadata/content adapter's raw abstract/description to
    `MAX_ABSTRACT_LENGTH` before `ExtractedContent` construction (review finding S3-R03)."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped[:MAX_ABSTRACT_LENGTH] or None


class EvidenceKind(StrEnum):
    FETCHED_CONTENT = "fetched_content"
    METADATA_LOOKUP = "metadata_lookup"


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class ExtractionEvidence(BaseModel):
    """Provenance for one adapter's contribution: what produced a fact, when, and a content
    hash proving what was actually read -- never the raw bytes/response body themselves."""

    kind: EvidenceKind
    adapter_name: str
    source_reference: str
    content_hash: str
    byte_length: int
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("adapter_name", "source_reference")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "ExtractionEvidence field")
        return _non_empty(value, f"ExtractionEvidence.{field_name}")

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        if len(value) != _CONTENT_HASH_LENGTH or any(
            character not in _CONTENT_HASH_ALPHABET for character in value
        ):
            raise InvariantViolationError(
                "ExtractionEvidence.content_hash must be 64 lowercase hex characters"
            )
        return value

    @field_validator("byte_length")
    @classmethod
    def _validate_byte_length(cls, value: int) -> int:
        if value < 0:
            raise InvariantViolationError("ExtractionEvidence.byte_length must not be negative")
        return value


class ExtractedContent(BaseModel):
    """Bounded structured evidence produced by one extraction call.

    Every populated field must trace back to at least one `ExtractionEvidence` entry; an
    adapter that found nothing usable raises instead of returning an empty/placeholder result.
    """

    resource_kind: ResourceKind
    canonical_identifier: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    published_at: datetime | None = None
    abstract: str | None = None
    body_markdown: str | None = None
    license_name: str | None = None
    primary_language: str | None = None
    star_count: int | None = None
    topics: tuple[str, ...] = ()
    evidence: tuple[ExtractionEvidence, ...]

    @field_validator("canonical_identifier")
    @classmethod
    def _validate_canonical_identifier(cls, value: str) -> str:
        return _non_empty(value, "ExtractedContent.canonical_identifier")

    @field_validator("title", "license_name", "primary_language")
    @classmethod
    def _validate_optional_non_empty(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        field_name = getattr(info, "field_name", "ExtractedContent field")
        return _non_empty(value, f"ExtractedContent.{field_name}")

    @field_validator("abstract")
    @classmethod
    def _validate_abstract(cls, value: str | None) -> str | None:
        if value is None:
            return value
        checked = _non_empty(value, "ExtractedContent.abstract")
        if len(checked) > MAX_ABSTRACT_LENGTH:
            raise InvariantViolationError(
                f"ExtractedContent.abstract must not exceed {MAX_ABSTRACT_LENGTH} characters"
            )
        return checked

    @field_validator("body_markdown")
    @classmethod
    def _validate_body_markdown(cls, value: str | None) -> str | None:
        if value is None:
            return value
        checked = _non_empty(value, "ExtractedContent.body_markdown")
        if len(checked) > MAX_BODY_MARKDOWN_LENGTH:
            raise InvariantViolationError(
                f"ExtractedContent.body_markdown must not exceed "
                f"{MAX_BODY_MARKDOWN_LENGTH} characters"
            )
        return checked

    @field_validator("authors", "topics")
    @classmethod
    def _validate_no_blank_entries(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "ExtractedContent field")
        for entry in value:
            _non_empty(entry, f"ExtractedContent.{field_name} entry")
        return value

    @field_validator("star_count")
    @classmethod
    def _validate_star_count(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise InvariantViolationError("ExtractedContent.star_count must not be negative")
        return value

    @field_validator("evidence")
    @classmethod
    def _validate_evidence_non_empty(
        cls, value: tuple[ExtractionEvidence, ...]
    ) -> tuple[ExtractionEvidence, ...]:
        if not value:
            raise InvariantViolationError(
                "ExtractedContent.evidence must not be empty: every extraction traces back to "
                "at least one adapter's provenance record"
            )
        return value
