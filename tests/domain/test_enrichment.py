from __future__ import annotations

from typing import Any

import pytest

from personal_graph_os.domain.enrichment import (
    MAX_LIST_ITEMS,
    MAX_PROPOSED_RELATIONS,
    MAX_SUMMARY_LENGTH,
    MAX_TAGS,
    ArticleEnrichmentPayload,
    EnrichmentResult,
    PaperEnrichmentPayload,
    ProposedRelation,
    ProposedRelationKind,
    RelationProposal,
    RelationProposalStatus,
    ResourceEnrichmentProfile,
    ResourceEnrichmentProfileVersion,
    UnsupportedResourceKindForEnrichmentError,
    payload_type_for_kind,
)
from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    NodeId,
    ResourceEnrichmentProfileId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.resource import ResourceKind


def test_payload_type_for_kind_rejects_an_unenrichable_resource_kind() -> None:
    with pytest.raises(UnsupportedResourceKindForEnrichmentError):
        payload_type_for_kind(ResourceKind.DOCUMENTATION)


def test_enrichment_result_rejects_a_payload_that_does_not_match_resource_kind() -> None:
    with pytest.raises(InvariantViolationError):
        EnrichmentResult(
            resource_kind=ResourceKind.PAPER,
            payload=ArticleEnrichmentPayload(summary="wrong shape"),
            evidence_content_hashes=("a" * 64,),
            confidence=0.9,
        )


def test_enrichment_result_rejects_empty_evidence() -> None:
    with pytest.raises(InvariantViolationError):
        EnrichmentResult(
            resource_kind=ResourceKind.PAPER,
            payload=PaperEnrichmentPayload(summary="a paper"),
            evidence_content_hashes=(),
            confidence=0.9,
        )


def test_enrichment_result_rejects_out_of_range_confidence() -> None:
    with pytest.raises(InvariantViolationError):
        EnrichmentResult(
            resource_kind=ResourceKind.PAPER,
            payload=PaperEnrichmentPayload(summary="a paper"),
            evidence_content_hashes=("a" * 64,),
            confidence=1.5,
        )


def _profile(**overrides: Any) -> ResourceEnrichmentProfile:
    defaults: dict[str, Any] = {
        "workspace_id": WorkspaceId(new_id()),
        "canonical_identifier": "arxiv:1",
        "resource_kind": ResourceKind.PAPER,
    }
    defaults.update(overrides)
    return ResourceEnrichmentProfile(**defaults)


def test_profile_starts_with_no_current_version() -> None:
    profile = _profile()
    assert profile.current_version_number == 0
    assert profile.current_version_id is None


def test_profile_rejects_version_number_without_version_id() -> None:
    with pytest.raises(InvariantViolationError):
        _profile(current_version_number=1)


def _version(
    profile_id: ResourceEnrichmentProfileId, **overrides: Any
) -> ResourceEnrichmentProfileVersion:
    defaults: dict[str, Any] = {
        "profile_id": profile_id,
        "version_number": 1,
        "resource_kind": ResourceKind.PAPER,
        "payload": PaperEnrichmentPayload(summary="a paper"),
        "evidence_content_hashes": ("a" * 64,),
        "provider_name": "fake",
        "confidence": 0.9,
        "created_by": "agent:test",
    }
    defaults.update(overrides)
    return ResourceEnrichmentProfileVersion(**defaults)


def test_profile_advances_to_the_next_version() -> None:
    profile = _profile()
    version = _version(profile.id)

    advanced = profile.with_new_current_version(version)

    assert advanced.current_version_number == 1
    assert advanced.current_version_id == version.id


def test_profile_rejects_advancing_to_a_non_sequential_version() -> None:
    profile = _profile()
    skipped_version = _version(profile.id, version_number=2)

    with pytest.raises(InvariantViolationError):
        profile.with_new_current_version(skipped_version)


def _relation_proposal(**overrides: Any) -> RelationProposal:
    defaults: dict[str, Any] = {
        "workspace_id": WorkspaceId(new_id()),
        "profile_version_id": _version(ResourceEnrichmentProfileId(new_id())).id,
        "source_node_id": NodeId(new_id()),
        "relation_kind": ProposedRelationKind.RELATES_TO,
        "candidate_label": "some-repo",
        "confidence": 0.9,
        "explanation": "mentioned prominently",
        "status": RelationProposalStatus.NEEDS_REVIEW,
    }
    defaults.update(overrides)
    return RelationProposal(**defaults)


def test_auto_applied_relation_proposal_requires_a_resolved_target() -> None:
    with pytest.raises(InvariantViolationError):
        _relation_proposal(status=RelationProposalStatus.AUTO_APPLIED)


def test_relation_proposal_cannot_relate_a_node_to_itself() -> None:
    node_id = NodeId(new_id())
    with pytest.raises(InvariantViolationError):
        _relation_proposal(
            source_node_id=node_id,
            resolved_target_node_id=node_id,
            status=RelationProposalStatus.AUTO_APPLIED,
        )


def test_relation_proposal_accepts_a_resolved_auto_applied_target() -> None:
    proposal = _relation_proposal(
        resolved_target_node_id=NodeId(new_id()), status=RelationProposalStatus.AUTO_APPLIED
    )
    assert proposal.status is RelationProposalStatus.AUTO_APPLIED


def _proposed_relation(**overrides: Any) -> ProposedRelation:
    defaults: dict[str, Any] = {
        "candidate_label": "some-repo",
        "relation_kind": ProposedRelationKind.RELATES_TO,
        "confidence": 0.9,
        "explanation": "mentioned prominently",
    }
    defaults.update(overrides)
    return ProposedRelation(**defaults)


def test_paper_payload_rejects_a_summary_longer_than_the_persisted_bound() -> None:
    """Review finding S4-R06: a faulty or adversarial provider must not be able to amplify
    bounded source input into an oversized persisted row."""
    with pytest.raises(InvariantViolationError):
        PaperEnrichmentPayload(summary="x" * (MAX_SUMMARY_LENGTH + 1))


def test_paper_payload_rejects_more_key_findings_than_the_persisted_bound() -> None:
    with pytest.raises(InvariantViolationError):
        PaperEnrichmentPayload(
            summary="a paper", key_findings=tuple(f"finding {i}" for i in range(MAX_LIST_ITEMS + 1))
        )


def test_enrichment_result_rejects_more_tags_than_the_persisted_bound() -> None:
    with pytest.raises(InvariantViolationError):
        EnrichmentResult(
            resource_kind=ResourceKind.PAPER,
            payload=PaperEnrichmentPayload(summary="a paper"),
            tags=tuple(f"tag{i}" for i in range(MAX_TAGS + 1)),
            evidence_content_hashes=("a" * 64,),
            confidence=0.9,
        )


def test_enrichment_result_rejects_more_proposed_relations_than_the_persisted_bound() -> None:
    with pytest.raises(InvariantViolationError):
        EnrichmentResult(
            resource_kind=ResourceKind.PAPER,
            payload=PaperEnrichmentPayload(summary="a paper"),
            evidence_content_hashes=("a" * 64,),
            confidence=0.9,
            proposed_relations=tuple(
                _proposed_relation(candidate_label=f"candidate-{i}")
                for i in range(MAX_PROPOSED_RELATIONS + 1)
            ),
        )
