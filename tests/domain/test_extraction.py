from __future__ import annotations

from typing import Any

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    hash_content,
)
from personal_graph_os.domain.resource import ResourceKind


def _evidence(**overrides: Any) -> ExtractionEvidence:
    defaults: dict[str, Any] = {
        "kind": EvidenceKind.FETCHED_CONTENT,
        "adapter_name": "test_adapter",
        "source_reference": "https://example.com/article",
        "content_hash": hash_content("hello world"),
        "byte_length": 11,
    }
    defaults.update(overrides)
    return ExtractionEvidence(**defaults)


def _content(**overrides: Any) -> ExtractedContent:
    defaults: dict[str, Any] = {
        "resource_kind": ResourceKind.ARTICLE,
        "canonical_identifier": "https://example.com/article",
        "evidence": (_evidence(),),
    }
    defaults.update(overrides)
    return ExtractedContent(**defaults)


def test_hash_content_is_stable_and_deterministic() -> None:
    assert hash_content("same input") == hash_content("same input")
    assert hash_content(b"same input") == hash_content("same input")
    assert hash_content("a") != hash_content("b")


def test_extraction_evidence_rejects_malformed_content_hash() -> None:
    with pytest.raises(InvariantViolationError):
        _evidence(content_hash="not-a-valid-hash")


def test_extraction_evidence_rejects_negative_byte_length() -> None:
    with pytest.raises(InvariantViolationError):
        _evidence(byte_length=-1)


def test_extraction_evidence_rejects_blank_adapter_name() -> None:
    with pytest.raises(InvariantViolationError):
        _evidence(adapter_name="  ")


def test_extracted_content_requires_at_least_one_evidence_entry() -> None:
    with pytest.raises(InvariantViolationError):
        _content(evidence=())


def test_extracted_content_rejects_body_markdown_over_the_bound() -> None:
    with pytest.raises(InvariantViolationError):
        _content(body_markdown="x" * 200_001)


def test_extracted_content_accepts_body_markdown_at_the_bound() -> None:
    content = _content(body_markdown="x" * 200_000)
    assert content.body_markdown is not None
    assert len(content.body_markdown) == 200_000


def test_extracted_content_rejects_blank_author_entry() -> None:
    with pytest.raises(InvariantViolationError):
        _content(authors=("Ada Lovelace", "  "))


def test_extracted_content_rejects_negative_star_count() -> None:
    with pytest.raises(InvariantViolationError):
        _content(star_count=-1)


def test_extracted_content_round_trips_bibliographic_fields() -> None:
    content = _content(
        title="A Paper",
        authors=("Ada Lovelace", "Alan Turing"),
        abstract="An abstract.",
        topics=("machine-learning",),
        star_count=42,
    )
    assert content.title == "A Paper"
    assert content.authors == ("Ada Lovelace", "Alan Turing")
    assert content.star_count == 42
