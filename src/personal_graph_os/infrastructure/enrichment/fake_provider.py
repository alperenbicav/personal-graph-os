"""A deterministic `EnrichmentProvider` test double: no network call, no model, no real cost
(EP-2026-012 ST-04). A real LLM-backed classifier is separate, future, separately-approved scope.

Deliberately demonstrates ST-04's prompt-injection boundary: it only ever reads
`ExtractedContent`'s own structurally-typed fields (title/abstract/body/topics) to build a
summary and never parses instructions out of them, and every proposed relation comes from an
explicit, test-supplied `auto_relate_to` list -- never derived from source text -- so nothing an
adversarial document says can change what gets auto-applied or how confident the result is.
"""

from __future__ import annotations

from personal_graph_os.domain.enrichment import (
    ArticleEnrichmentPayload,
    EnrichmentResult,
    PaperEnrichmentPayload,
    ProposedRelation,
    ProposedRelationKind,
    RepositoryEnrichmentPayload,
    UnsupportedResourceKindForEnrichmentError,
)
from personal_graph_os.domain.extraction import ExtractedContent
from personal_graph_os.domain.resource import ResourceKind

_SUMMARY_MAX_LENGTH = 280


class FakeEnrichmentProvider:
    def __init__(
        self,
        *,
        name: str = "fake",
        confidence: float = 0.9,
        auto_relate_to: tuple[tuple[str, ProposedRelationKind, float], ...] = (),
    ) -> None:
        self._name = name
        self._confidence = confidence
        # (candidate_label, relation_kind, confidence) tuples a caller wires in explicitly, so
        # relation-resolution/auto-apply behavior in a test is test-controlled, never guessed
        # from source text.
        self._auto_relate_to = auto_relate_to

    @property
    def name(self) -> str:
        return self._name

    def classify(self, *, extracted: ExtractedContent) -> EnrichmentResult:
        summary = self._build_summary(extracted)
        tags = extracted.topics if extracted.topics else ("uncategorized",)
        evidence_content_hashes = tuple(evidence.content_hash for evidence in extracted.evidence)
        proposed_relations = tuple(
            ProposedRelation(
                candidate_label=label,
                relation_kind=relation_kind,
                confidence=relation_confidence,
                explanation=f"configured test relation to {label!r}",
            )
            for label, relation_kind, relation_confidence in self._auto_relate_to
        )
        return EnrichmentResult(
            resource_kind=extracted.resource_kind,
            payload=self._build_payload(extracted, summary),
            tags=tags,
            evidence_content_hashes=evidence_content_hashes,
            confidence=self._confidence,
            proposed_relations=proposed_relations,
        )

    def _build_summary(self, extracted: ExtractedContent) -> str:
        candidate = extracted.abstract or extracted.body_markdown or extracted.title
        source = candidate if candidate is not None else extracted.canonical_identifier
        return source.strip()[:_SUMMARY_MAX_LENGTH]

    def _build_payload(
        self, extracted: ExtractedContent, summary: str
    ) -> PaperEnrichmentPayload | ArticleEnrichmentPayload | RepositoryEnrichmentPayload:
        if extracted.resource_kind is ResourceKind.PAPER:
            return PaperEnrichmentPayload(summary=summary, key_findings=extracted.topics[:3])
        if extracted.resource_kind is ResourceKind.ARTICLE:
            return ArticleEnrichmentPayload(summary=summary, key_findings=extracted.topics[:3])
        if extracted.resource_kind is ResourceKind.GITHUB_REPOSITORY:
            return RepositoryEnrichmentPayload(
                summary=summary,
                capabilities=extracted.topics[:5],
                architecture_summary=extracted.primary_language,
            )
        raise UnsupportedResourceKindForEnrichmentError(
            f"FakeEnrichmentProvider cannot classify resource kind {extracted.resource_kind}"
        )
