"""Agentic enrichment and relation engine (EP-2026-012 ST-04).

A `ResourceEnrichmentProfile` is one Resource's enrichment identity, keyed by
`(workspace_id, canonical_identifier)` -- deliberately not `resource_id`/`node_id`, so a graph
node being deleted and recreated (the same resource re-captured) never loses its enrichment
history. Its content lives entirely in immutable, numbered `ResourceEnrichmentProfileVersion`
rows, mirroring `domain.documents.Document`/`DocumentVersion`: the profile identity tracks only
which version is current, never the content itself.

A `RelationProposal` is one candidate edge a classifier suggested. The classifier never resolves
a `candidate_label` to a real `NodeId` itself -- only `EnrichmentService` does that, by exact
identifier match against this workspace's own resources -- so text embedded in a captured source
can never select a graph node, execute a tool, or force auto-apply on its own; confidence is a
separate, structurally typed field the classifier emits, never something parsed out of body text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from personal_graph_os.domain.errors import DomainError, InvariantViolationError
from personal_graph_os.domain.identifiers import (
    NodeId,
    RelationProposalId,
    ResourceEnrichmentProfileId,
    ResourceEnrichmentProfileVersionId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.resource import ResourceKind

# Resource kinds ST-04's agentic enrichment supports today; the epic's other kinds (documentation,
# specification, dataset, video, book, other) have no agentic profile shape defined yet.
ENRICHABLE_RESOURCE_KINDS = frozenset(
    {ResourceKind.PAPER, ResourceKind.ARTICLE, ResourceKind.GITHUB_REPOSITORY}
)

# Persisted-output bounds (review finding S4-R06): a faulty or adversarial provider must never be
# able to amplify bounded source input into oversized rows or a Review Inbox flood. Every provider
# result is truncated/rejected against these at the domain boundary, never trusted as-is.
MAX_SUMMARY_LENGTH = 4_000
MAX_TEXT_FIELD_LENGTH = 2_000
MAX_LIST_ITEM_LENGTH = 300
MAX_LIST_ITEMS = 25
MAX_TAGS = 25
MAX_EXPLANATION_LENGTH = 500
MAX_CANDIDATE_LABEL_LENGTH = 500
MAX_PROPOSED_RELATIONS = 20
# Bibliographic/evidence bounds (review finding S6-R01): authors/abstract are copied verbatim
# from the resource's own `ExtractedContent` (deterministic adapter output), never asked of or
# trusted from the classifier, so they get their own bounds independent of the LLM-facing ones
# above. `MAX_ABSTRACT_LENGTH` matches `domain.extraction`'s own bound on the same field.
MAX_AUTHORS = 25
MAX_AUTHOR_LENGTH = 300
MAX_ABSTRACT_LENGTH = 20_000
_CONTENT_HASH_LENGTH = 64
_CONTENT_HASH_ALPHABET = frozenset("0123456789abcdef")


class EnrichmentError(DomainError):
    """Base error for a resource this layer could not enrich."""


class UnsupportedResourceKindForEnrichmentError(EnrichmentError):
    """Raised when asked to enrich a `ResourceKind` outside `ENRICHABLE_RESOURCE_KINDS`."""


class UncitedEnrichmentEvidenceError(EnrichmentError):
    """Raised when a classifier result cites an evidence content hash that does not trace back
    to any `ExtractionEvidence` entry on the resource's own `ExtractedContent` -- every
    generated field must be evidence-backed, never fabricated from nothing."""


class ConcurrentEnrichmentUpdateError(EnrichmentError):
    """Raised when creating a new profile version whose expected current version number no
    longer matches the profile's actual current version -- another write already landed, so
    this one must re-read and retry rather than silently overwrite it."""


class EnrichmentSourceMismatchError(EnrichmentError):
    """Raised when the `ExtractedContent` passed to `enrich_resource` does not belong to the
    requested Resource (its `canonical_identifier` or `resource_kind` differs) -- review finding
    S4-R01: without this check, one resource's evidence/content could be classified and persisted
    under a different resource's canonical profile, poisoning it with unrelated content."""


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


def _validate_confidence(value: float, field_label: str) -> float:
    if not (0.0 <= value <= 1.0):
        raise InvariantViolationError(f"{field_label} must be between 0.0 and 1.0, got {value}")
    return value


def _validate_bounded_text(value: str, max_length: int, field_label: str) -> str:
    if len(value) > max_length:
        raise InvariantViolationError(
            f"{field_label} must not exceed {max_length} characters, got {len(value)}"
        )
    return value


def _validate_bounded_optional_text(
    value: str | None, max_length: int, field_label: str
) -> str | None:
    if value is None:
        return value
    return _validate_bounded_text(value, max_length, field_label)


def _validate_bounded_collection(
    value: tuple[str, ...], *, max_items: int, max_item_length: int, field_label: str
) -> tuple[str, ...]:
    if len(value) > max_items:
        raise InvariantViolationError(
            f"{field_label} must not exceed {max_items} entries, got {len(value)}"
        )
    for entry in value:
        _validate_bounded_text(entry, max_item_length, f"{field_label} entry")
    return value


def truncate_authors(authors: tuple[str, ...]) -> tuple[str, ...]:
    """Deterministically bound a source-derived author list to what
    `ResourceEnrichmentProfileVersion.authors` accepts (review finding S6-R04):
    `domain.extraction.ExtractedContent.authors` places no upper bound on author count or name
    length, since real consortium papers can list hundreds of authors, but a faulty/adversarial
    count must never fail persistence and discard an otherwise-successful enrichment outright.
    Mirrors the existing truncate-before-construction pattern (`domain.extraction`'s
    `truncate_body_markdown`/`truncate_abstract`, review finding S3-R03) rather than rejecting."""
    return tuple(author[:MAX_AUTHOR_LENGTH] for author in authors[:MAX_AUTHORS])


class PaperEnrichmentPayload(BaseModel):
    """Typed enrichment fields for `ResourceKind.PAPER`."""

    summary: str
    key_findings: tuple[str, ...] = ()
    methodology: str | None = None
    limitations: tuple[str, ...] = ()
    applicability: str | None = None

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "PaperEnrichmentPayload.summary"),
            MAX_SUMMARY_LENGTH,
            "PaperEnrichmentPayload.summary",
        )

    @field_validator("key_findings", "limitations")
    @classmethod
    def _validate_lists(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "PaperEnrichmentPayload field")
        return _validate_bounded_collection(
            value,
            max_items=MAX_LIST_ITEMS,
            max_item_length=MAX_LIST_ITEM_LENGTH,
            field_label=f"PaperEnrichmentPayload.{field_name}",
        )

    @field_validator("methodology", "applicability")
    @classmethod
    def _validate_optional_text(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "PaperEnrichmentPayload field")
        return _validate_bounded_optional_text(
            value, MAX_TEXT_FIELD_LENGTH, f"PaperEnrichmentPayload.{field_name}"
        )


class ArticleEnrichmentPayload(BaseModel):
    """Typed enrichment fields for `ResourceKind.ARTICLE`."""

    summary: str
    key_findings: tuple[str, ...] = ()
    applicability: str | None = None

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "ArticleEnrichmentPayload.summary"),
            MAX_SUMMARY_LENGTH,
            "ArticleEnrichmentPayload.summary",
        )

    @field_validator("key_findings")
    @classmethod
    def _validate_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_bounded_collection(
            value,
            max_items=MAX_LIST_ITEMS,
            max_item_length=MAX_LIST_ITEM_LENGTH,
            field_label="ArticleEnrichmentPayload.key_findings",
        )

    @field_validator("applicability")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        return _validate_bounded_optional_text(
            value, MAX_TEXT_FIELD_LENGTH, "ArticleEnrichmentPayload.applicability"
        )


class RepositoryEnrichmentPayload(BaseModel):
    """Typed enrichment fields for `ResourceKind.GITHUB_REPOSITORY`.

    `tech_stack`/`license_name`/`activity_summary` (EP-2026-012 ST-06) fill the Repositories
    workspace's stack/license/activity acceptance fields; both are optional and additive to the
    ST-04 shape, so an existing `ResourceEnrichmentProfileVersion` row (JSON-serialized, no
    schema migration involved) still deserializes with them defaulting to unset.
    """

    summary: str
    capabilities: tuple[str, ...] = ()
    architecture_summary: str | None = None
    risks: tuple[str, ...] = ()
    applicability: str | None = None
    tech_stack: tuple[str, ...] = ()
    license_name: str | None = None
    activity_summary: str | None = None

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "RepositoryEnrichmentPayload.summary"),
            MAX_SUMMARY_LENGTH,
            "RepositoryEnrichmentPayload.summary",
        )

    @field_validator("capabilities", "risks", "tech_stack")
    @classmethod
    def _validate_lists(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "RepositoryEnrichmentPayload field")
        return _validate_bounded_collection(
            value,
            max_items=MAX_LIST_ITEMS,
            max_item_length=MAX_LIST_ITEM_LENGTH,
            field_label=f"RepositoryEnrichmentPayload.{field_name}",
        )

    @field_validator("architecture_summary", "applicability", "license_name", "activity_summary")
    @classmethod
    def _validate_optional_text(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "RepositoryEnrichmentPayload field")
        return _validate_bounded_optional_text(
            value, MAX_TEXT_FIELD_LENGTH, f"RepositoryEnrichmentPayload.{field_name}"
        )


EnrichmentPayload = PaperEnrichmentPayload | ArticleEnrichmentPayload | RepositoryEnrichmentPayload

_PAYLOAD_TYPE_BY_KIND: dict[ResourceKind, type[EnrichmentPayload]] = {
    ResourceKind.PAPER: PaperEnrichmentPayload,
    ResourceKind.ARTICLE: ArticleEnrichmentPayload,
    ResourceKind.GITHUB_REPOSITORY: RepositoryEnrichmentPayload,
}


def payload_type_for_kind(resource_kind: ResourceKind) -> type[EnrichmentPayload]:
    """The typed payload class `resource_kind` must use. Raises
    `UnsupportedResourceKindForEnrichmentError` for a kind ST-04 does not enrich."""
    payload_type = _PAYLOAD_TYPE_BY_KIND.get(resource_kind)
    if payload_type is None:
        raise UnsupportedResourceKindForEnrichmentError(
            f"resource kind {resource_kind} has no agentic enrichment payload shape"
        )
    return payload_type


class ResourceEnrichmentProfile(BaseModel):
    """One resource's enrichment identity: which version is current, never the content itself.

    Keyed by `(workspace_id, canonical_identifier)`, not `resource_id`/`node_id` (EP-2026-012
    ST-04 selected approach), so this identity -- and the version history it points at --
    survives the backing graph node being deleted and recreated for the same captured source.
    """

    id: ResourceEnrichmentProfileId = Field(
        default_factory=lambda: ResourceEnrichmentProfileId(new_id())
    )
    workspace_id: WorkspaceId
    canonical_identifier: str
    resource_kind: ResourceKind
    current_version_number: int = 0
    current_version_id: ResourceEnrichmentProfileVersionId | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("canonical_identifier")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        return _non_empty(value, "ResourceEnrichmentProfile.canonical_identifier")

    @field_validator("current_version_number")
    @classmethod
    def _validate_version_number(cls, value: int) -> int:
        if value < 0:
            raise InvariantViolationError(
                "ResourceEnrichmentProfile.current_version_number must not be negative"
            )
        return value

    @model_validator(mode="after")
    def _validate_current_version_consistency(self) -> ResourceEnrichmentProfile:
        has_version_number = self.current_version_number > 0
        has_version_id = self.current_version_id is not None
        if has_version_number != has_version_id:
            raise InvariantViolationError(
                f"ResourceEnrichmentProfile {self.id} must declare both "
                "current_version_number and current_version_id together, or neither"
            )
        return self

    def with_new_current_version(
        self, version: ResourceEnrichmentProfileVersion
    ) -> ResourceEnrichmentProfile:
        """Return a copy advanced to `version`, which must be exactly the next version number."""
        if version.version_number != self.current_version_number + 1:
            raise InvariantViolationError(
                f"ResourceEnrichmentProfile {self.id} expected version "
                f"{self.current_version_number + 1}, got {version.version_number}"
            )
        return self.model_copy(
            update={
                "current_version_number": version.version_number,
                "current_version_id": version.id,
                "updated_at": datetime.now(UTC),
            }
        )


class CitedEvidenceReference(BaseModel):
    """A copyright-safe, inspectable record of one cited evidence entry (review finding
    S6-R01): adapter identity, source reference, content hash, and retrieval time -- mirroring
    `domain.extraction.ExtractionEvidence`'s own shape but declared independently here (this
    module never imports the extraction domain) -- never the raw fetched bytes/response body,
    so a persisted profile version can prove *what was read* without redistributing it."""

    adapter_name: str
    source_reference: str
    content_hash: str
    retrieved_at: datetime

    @field_validator("adapter_name", "source_reference")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "CitedEvidenceReference field")
        return _non_empty(value, f"CitedEvidenceReference.{field_name}")

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        if len(value) != _CONTENT_HASH_LENGTH or any(
            character not in _CONTENT_HASH_ALPHABET for character in value
        ):
            raise InvariantViolationError(
                "CitedEvidenceReference.content_hash must be 64 lowercase hex characters"
            )
        return value


class ResourceEnrichmentProfileVersion(BaseModel):
    """One immutable, numbered enrichment result for a `ResourceEnrichmentProfile`.

    `authors`/`published_at`/`abstract` (review finding S6-R01) are copied verbatim from the
    resource's own `ExtractedContent` by `EnrichmentService` itself, never asked of or trusted
    from the classifier -- deterministic bibliographic facts are not something an LLM should be
    able to assert or alter. `cited_evidence` is the inspectable counterpart to
    `evidence_content_hashes` (kept for the existing cite-validation contract): the same hashes,
    with adapter/source/retrieval provenance attached, so a UI can show what was actually read
    without exposing raw content.
    """

    id: ResourceEnrichmentProfileVersionId = Field(
        default_factory=lambda: ResourceEnrichmentProfileVersionId(new_id())
    )
    profile_id: ResourceEnrichmentProfileId
    version_number: int
    resource_kind: ResourceKind
    payload: EnrichmentPayload
    tags: tuple[str, ...] = ()
    evidence_content_hashes: tuple[str, ...]
    cited_evidence: tuple[CitedEvidenceReference, ...] = ()
    authors: tuple[str, ...] = ()
    published_at: datetime | None = None
    abstract: str | None = None
    provider_name: str
    model_name: str | None = None
    confidence: float
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("version_number")
    @classmethod
    def _validate_version_number(cls, value: int) -> int:
        if value < 1:
            raise InvariantViolationError(
                "ResourceEnrichmentProfileVersion.version_number must be >= 1"
            )
        return value

    @field_validator("provider_name", "created_by")
    @classmethod
    def _validate_non_empty(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "ResourceEnrichmentProfileVersion field")
        return _non_empty(value, f"ResourceEnrichmentProfileVersion.{field_name}")

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for entry in value:
            _non_empty(entry, "ResourceEnrichmentProfileVersion.tags entry")
        return _validate_bounded_collection(
            value,
            max_items=MAX_TAGS,
            max_item_length=MAX_LIST_ITEM_LENGTH,
            field_label="ResourceEnrichmentProfileVersion.tags",
        )

    @field_validator("evidence_content_hashes")
    @classmethod
    def _validate_evidence_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise InvariantViolationError(
                "ResourceEnrichmentProfileVersion.evidence_content_hashes must not be empty: "
                "every generated profile traces back to at least one extraction evidence entry"
            )
        return value

    @field_validator("authors")
    @classmethod
    def _validate_authors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for entry in value:
            _non_empty(entry, "ResourceEnrichmentProfileVersion.authors entry")
        return _validate_bounded_collection(
            value,
            max_items=MAX_AUTHORS,
            max_item_length=MAX_AUTHOR_LENGTH,
            field_label="ResourceEnrichmentProfileVersion.authors",
        )

    @field_validator("abstract")
    @classmethod
    def _validate_abstract(cls, value: str | None) -> str | None:
        return _validate_bounded_optional_text(
            value, MAX_ABSTRACT_LENGTH, "ResourceEnrichmentProfileVersion.abstract"
        )

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        return _validate_confidence(value, "ResourceEnrichmentProfileVersion.confidence")

    @model_validator(mode="after")
    def _validate_payload_matches_kind(self) -> ResourceEnrichmentProfileVersion:
        expected_type = payload_type_for_kind(self.resource_kind)
        if not isinstance(self.payload, expected_type):
            raise InvariantViolationError(
                f"ResourceEnrichmentProfileVersion {self.id} declares resource_kind "
                f"{self.resource_kind} but payload is {type(self.payload).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return self

    @model_validator(mode="after")
    def _validate_cited_evidence_matches_hashes(self) -> ResourceEnrichmentProfileVersion:
        if not self.cited_evidence:
            return self
        cited_hashes = {reference.content_hash for reference in self.cited_evidence}
        if cited_hashes != set(self.evidence_content_hashes):
            raise InvariantViolationError(
                f"ResourceEnrichmentProfileVersion {self.id}'s cited_evidence hashes "
                f"{sorted(cited_hashes)} must exactly match evidence_content_hashes "
                f"{sorted(self.evidence_content_hashes)} when cited_evidence is populated"
            )
        return self


class ProposedRelationKind(StrEnum):
    """The bounded vocabulary a classifier may propose. Each name matches an existing
    `EdgeType.name` already seeded by `default_schema.py` -- ST-04 introduces no new edge-type
    vocabulary, it only proposes typed instances of what already exists."""

    RELATES_TO = "relates_to"
    CITES = "cites"


class ProposedRelation(BaseModel):
    """One candidate relation from a classifier result, before resolution.

    `candidate_label` is free text (a title, URL, or identifier a source document mentioned) --
    never a `NodeId` -- because the classifier has no database access and must not be able to
    address an internal object directly. Only `EnrichmentService` turns a label into an actual
    node, by exact identifier match against this workspace's own resources.

    This model deliberately carries no classifier-asserted "evidence" field (review finding
    S4-R02, round 2): an earlier design let a classifier cite one of a whitelist of evidence
    hashes it was handed verbatim in the prompt, which a manipulated model could satisfy by simply
    echoing a valid-looking value back -- proving nothing about whether the *relation itself* is
    actually supported. Grounding is instead computed independently by
    `EnrichmentService._is_independently_grounded()` from the resource's own extracted text,
    never asserted by the classifier.
    """

    candidate_label: str
    relation_kind: ProposedRelationKind
    confidence: float
    explanation: str

    @field_validator("candidate_label")
    @classmethod
    def _validate_candidate_label(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "ProposedRelation.candidate_label"),
            MAX_CANDIDATE_LABEL_LENGTH,
            "ProposedRelation.candidate_label",
        )

    @field_validator("explanation")
    @classmethod
    def _validate_explanation(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "ProposedRelation.explanation"),
            MAX_EXPLANATION_LENGTH,
            "ProposedRelation.explanation",
        )

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        return _validate_confidence(value, "ProposedRelation.confidence")


class EnrichmentResult(BaseModel):
    """The bounded, typed shape every `EnrichmentProvider` returns: a payload matching the
    requested resource kind, tags, cited evidence, overall confidence, and candidate relations.
    Structurally validated by Pydantic -- there is no free-form or executable field a classifier
    could use to do anything beyond populate these typed values."""

    resource_kind: ResourceKind
    payload: EnrichmentPayload
    tags: tuple[str, ...] = ()
    evidence_content_hashes: tuple[str, ...]
    confidence: float
    proposed_relations: tuple[ProposedRelation, ...] = ()

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for entry in value:
            _non_empty(entry, "EnrichmentResult.tags entry")
        return _validate_bounded_collection(
            value,
            max_items=MAX_TAGS,
            max_item_length=MAX_LIST_ITEM_LENGTH,
            field_label="EnrichmentResult.tags",
        )

    @field_validator("evidence_content_hashes")
    @classmethod
    def _validate_evidence_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise InvariantViolationError(
                "EnrichmentResult.evidence_content_hashes must not be empty"
            )
        return value

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        return _validate_confidence(value, "EnrichmentResult.confidence")

    @field_validator("proposed_relations")
    @classmethod
    def _validate_proposed_relations_bounded(
        cls, value: tuple[ProposedRelation, ...]
    ) -> tuple[ProposedRelation, ...]:
        if len(value) > MAX_PROPOSED_RELATIONS:
            raise InvariantViolationError(
                f"EnrichmentResult.proposed_relations must not exceed {MAX_PROPOSED_RELATIONS} "
                f"entries, got {len(value)}"
            )
        return value

    @model_validator(mode="after")
    def _validate_payload_matches_kind(self) -> EnrichmentResult:
        expected_type = payload_type_for_kind(self.resource_kind)
        if not isinstance(self.payload, expected_type):
            raise InvariantViolationError(
                f"EnrichmentResult declares resource_kind {self.resource_kind} but payload is "
                f"{type(self.payload).__name__}, expected {expected_type.__name__}"
            )
        return self


class RelationProposalStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    AUTO_APPLIED = "auto_applied"


class RelationProposal(BaseModel):
    """One persisted Review Inbox row (or auto-applied record) for a candidate relation.

    Keyed to the *live* `source_node_id`, unlike `ResourceEnrichmentProfile`: an unresolved
    proposal about a node that no longer exists is not meaningful history to retain.
    """

    id: RelationProposalId = Field(default_factory=lambda: RelationProposalId(new_id()))
    workspace_id: WorkspaceId
    profile_version_id: ResourceEnrichmentProfileVersionId
    source_node_id: NodeId
    relation_kind: ProposedRelationKind
    candidate_label: str
    confidence: float
    explanation: str
    status: RelationProposalStatus
    resolved_target_node_id: NodeId | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("candidate_label")
    @classmethod
    def _validate_candidate_label(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "RelationProposal.candidate_label"),
            MAX_CANDIDATE_LABEL_LENGTH,
            "RelationProposal.candidate_label",
        )

    @field_validator("explanation")
    @classmethod
    def _validate_explanation(cls, value: str) -> str:
        return _validate_bounded_text(
            _non_empty(value, "RelationProposal.explanation"),
            MAX_EXPLANATION_LENGTH,
            "RelationProposal.explanation",
        )

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        return _validate_confidence(value, "RelationProposal.confidence")

    @model_validator(mode="after")
    def _validate_auto_applied_has_target(self) -> RelationProposal:
        if (
            self.status is RelationProposalStatus.AUTO_APPLIED
            and self.resolved_target_node_id is None
        ):
            raise InvariantViolationError(
                f"RelationProposal {self.id} is auto_applied but declares no "
                "resolved_target_node_id: auto-apply only ever follows a resolved match"
            )
        if self.resolved_target_node_id == self.source_node_id:
            raise InvariantViolationError(
                f"RelationProposal {self.id} cannot relate node {self.source_node_id} to itself"
            )
        return self
