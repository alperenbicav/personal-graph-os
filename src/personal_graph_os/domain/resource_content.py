"""Persisted source content for one Resource (EP-2026-012 ST-13).

Extraction (`ExtractionService`) turns a resource's canonical identifier/source URL into a
bounded `ExtractedContent`; until ST-13 only its content hash survived as evidence while the
readable body was discarded after enrichment. `ResourceContent` keeps that bounded body so the
reader can show the full source text without re-fetching on every open. It is derived evidence,
never user-authored state: it is reproducible from the source, carries the content hash and
adapter/retrieval provenance of exactly what was read, and stays within the same
`MAX_BODY_MARKDOWN_LENGTH` bound the extraction layer enforces. It is stored for local
single-user reading and is never redistributed into generated records.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.extraction import MAX_BODY_MARKDOWN_LENGTH
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId


class ResourceContent(BaseModel):
    """One resource's persisted source text plus the provenance of the fetch that read it."""

    resource_id: ResourceId
    workspace_id: WorkspaceId
    body_markdown: str
    content_hash: str
    adapter_name: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("body_markdown")
    @classmethod
    def _validate_body_markdown(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("ResourceContent.body_markdown must not be empty")
        if len(stripped) > MAX_BODY_MARKDOWN_LENGTH:
            raise InvariantViolationError(
                "ResourceContent.body_markdown must not exceed "
                f"{MAX_BODY_MARKDOWN_LENGTH} characters"
            )
        return stripped

    @field_validator("content_hash", "adapter_name")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "ResourceContent field")
        if not value.strip():
            raise InvariantViolationError(f"ResourceContent.{field_name} must not be empty")
        return value
