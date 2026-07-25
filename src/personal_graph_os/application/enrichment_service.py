"""Orchestrates one resource's agentic enrichment pass (EP-2026-012 ST-04).

`EnrichmentService` is the only composition point between an `EnrichmentProvider` classifier and
canonical state: it validates the classifier's cited evidence, creates the next immutable
`ResourceEnrichmentProfileVersion` under expected-version protection, resolves each proposed
relation to a real node *itself* (a classifier never gets to address a node directly -- see
`application.enrichment_adapters`), auto-applies high-confidence resolved relations as `Edge`s,
records every proposal in the persisted Review Inbox, and best-effort projects two selected
display fields onto the resource's `Node`. All of it commits or rolls back together through one
`ResearchUnitOfWork`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from personal_graph_os.application.enrichment_adapters import EnrichmentProvider
from personal_graph_os.application.repositories import WorkspaceRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.services import ResourceNotFoundError, WorkspaceNotFoundError
from personal_graph_os.domain.enrichment import (
    ENRICHABLE_RESOURCE_KINDS,
    CitedEvidenceReference,
    EnrichmentError,
    EnrichmentResult,
    EnrichmentSourceMismatchError,
    ProposedRelation,
    ProposedRelationKind,
    RelationProposal,
    RelationProposalStatus,
    ResourceEnrichmentProfile,
    ResourceEnrichmentProfileVersion,
    UncitedEnrichmentEvidenceError,
    UnsupportedResourceKindForEnrichmentError,
    truncate_authors,
)
from personal_graph_os.domain.extraction import ExtractedContent
from personal_graph_os.domain.graph import Edge
from personal_graph_os.domain.identifiers import EdgeTypeId, NodeId, ResourceId, WorkspaceId
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.schema import FieldDefinition, NodeType, Workspace

_ENRICHMENT_SUMMARY_FIELD_NAME = "enrichment_summary"
_ENRICHMENT_CONFIDENCE_FIELD_NAME = "enrichment_confidence"
_PROJECTED_SUMMARY_MAX_LENGTH = 500

# The configured relation-policy default (review finding S4-R02): only these relation kinds are
# ever eligible for auto-apply at all, independent of confidence or grounding. A caller may narrow
# this further per workspace; it is never widened past `ProposedRelationKind`'s own vocabulary.
DEFAULT_AUTO_APPLY_RELATION_KINDS = frozenset(
    {ProposedRelationKind.RELATES_TO, ProposedRelationKind.CITES}
)


class EnrichmentNotConfiguredError(RuntimeError):
    """Raised by the enrichment route when no `EnrichmentProvider` is configured
    (`PGOS_ENRICHMENT_PROVIDER` unset or blank) -- the default, fail-closed, local-only state
    (review finding S4-R03): the composition root never falls back to a fake/no-op provider, it
    simply refuses the operation."""


class EnrichmentOutcome:
    """The result of one completed enrichment pass."""

    def __init__(
        self,
        *,
        profile: ResourceEnrichmentProfile,
        version: ResourceEnrichmentProfileVersion,
        relation_proposals: tuple[RelationProposal, ...],
    ) -> None:
        self.profile = profile
        self.version = version
        self.relation_proposals = relation_proposals


class EnrichmentService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        provider: EnrichmentProvider,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        *,
        auto_apply_confidence_threshold: float = 0.8,
        auto_apply_relation_kinds: frozenset[ProposedRelationKind] = (
            DEFAULT_AUTO_APPLY_RELATION_KINDS
        ),
    ) -> None:
        self._workspaces = workspaces
        self._provider = provider
        self._unit_of_work_factory = unit_of_work_factory
        self._auto_apply_confidence_threshold = auto_apply_confidence_threshold
        self._auto_apply_relation_kinds = auto_apply_relation_kinds

    def _require_workspace(self, workspace_id: WorkspaceId) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")
        return workspace

    def _fetch_and_validate_resource(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace_id: WorkspaceId,
        resource_id: ResourceId,
        extracted: ExtractedContent,
    ) -> Resource:
        """Fetch `resource_id` and require it to belong to `workspace_id`, and require
        `extracted` to actually belong to it.

        Raises `ResourceNotFoundError` (review finding S4-R07: a resource that exists but
        belongs to a *different* workspace is reported identically to one that does not exist
        at all, matching `ContextPackService._require_owned_evidence_pointer`'s convention,
        rather than a distinct error that would confirm its existence across a workspace
        boundary), `UnsupportedResourceKindForEnrichmentError`, or
        `EnrichmentSourceMismatchError` (review finding S4-R01) -- checked before any provider
        invocation or write, and re-checked at write time (review finding S4-R04) so a resource
        that changed or disappeared between the two phases is never silently enriched anyway.
        """
        resource = unit_of_work.resources.get(resource_id)
        if resource is None or resource.workspace_id != workspace_id:
            raise ResourceNotFoundError(
                f"resource {resource_id} does not exist in workspace {workspace_id}"
            )
        if resource.kind not in ENRICHABLE_RESOURCE_KINDS:
            raise UnsupportedResourceKindForEnrichmentError(
                f"resource {resource_id} has kind {resource.kind}, which ST-04 does not "
                "agentically enrich"
            )
        if (
            extracted.canonical_identifier != resource.canonical_identifier
            or extracted.resource_kind != resource.kind
        ):
            raise EnrichmentSourceMismatchError(
                f"extracted content identifies "
                f"{extracted.resource_kind}:{extracted.canonical_identifier!r} but resource "
                f"{resource_id} is {resource.kind}:{resource.canonical_identifier!r}"
            )
        return resource

    def enrich_resource(
        self,
        *,
        workspace_id: WorkspaceId,
        resource_id: ResourceId,
        extracted: ExtractedContent,
        actor: str,
    ) -> EnrichmentOutcome:
        """Classify `extracted` and persist the resulting profile version, relation proposals,
        and node projection as one atomic pass.

        Raises `UnsupportedResourceKindForEnrichmentError` for a resource kind ST-04 does not
        enrich, `EnrichmentSourceMismatchError` if `extracted` does not belong to this resource,
        `UncitedEnrichmentEvidenceError` if the classifier cites evidence this resource's own
        extraction never produced, and `ConcurrentEnrichmentUpdateError` if another enrichment
        write landed for this resource between read and write.

        The classifier call happens entirely outside any database transaction (review finding
        S4-R04): a real network/model call must never hold a write lock, so this method opens a
        short read-only unit of work to validate the resource first, classifies with no
        transaction open, then opens a second, short unit of work that re-validates the resource
        before the atomic write phase.
        """
        workspace = self._require_workspace(workspace_id)

        with self._unit_of_work_factory() as unit_of_work:
            self._fetch_and_validate_resource(unit_of_work, workspace_id, resource_id, extracted)

        result = self._provider.classify(extracted=extracted)
        if result.resource_kind != extracted.resource_kind:
            raise EnrichmentError(
                f"classifier returned resource_kind {result.resource_kind} for extracted "
                f"content of kind {extracted.resource_kind}"
            )
        self._require_cited_evidence_known(result, extracted)

        with self._unit_of_work_factory() as unit_of_work:
            resource = self._fetch_and_validate_resource(
                unit_of_work, workspace_id, resource_id, extracted
            )

            profile = unit_of_work.resource_enrichment_profiles.get_by_identifier(
                workspace_id, resource.canonical_identifier
            )
            if profile is None:
                profile = ResourceEnrichmentProfile(
                    workspace_id=workspace_id,
                    canonical_identifier=resource.canonical_identifier,
                    resource_kind=resource.kind,
                )
                unit_of_work.resource_enrichment_profiles.save_without_commit(profile)

            expected_version_number = profile.current_version_number
            cited_hashes = set(result.evidence_content_hashes)
            cited_evidence = tuple(
                CitedEvidenceReference(
                    adapter_name=evidence.adapter_name,
                    source_reference=evidence.source_reference,
                    content_hash=evidence.content_hash,
                    retrieved_at=evidence.retrieved_at,
                )
                for evidence in extracted.evidence
                if evidence.content_hash in cited_hashes
            )
            version = ResourceEnrichmentProfileVersion(
                profile_id=profile.id,
                version_number=expected_version_number + 1,
                resource_kind=resource.kind,
                payload=result.payload,
                tags=result.tags,
                evidence_content_hashes=result.evidence_content_hashes,
                cited_evidence=cited_evidence,
                authors=truncate_authors(extracted.authors),
                published_at=extracted.published_at,
                abstract=extracted.abstract,
                provider_name=self._provider.name,
                confidence=result.confidence,
                created_by=actor,
            )
            unit_of_work.resource_enrichment_profile_versions.create_version_without_commit(
                version, expected_current_version_number=expected_version_number
            )
            updated_profile = profile.with_new_current_version(version)
            unit_of_work.resource_enrichment_profiles.save_without_commit(updated_profile)

            relation_proposals = tuple(
                self._resolve_and_persist_relation(
                    unit_of_work, workspace, resource, extracted, version, proposed
                )
                for proposed in result.proposed_relations
            )

            self._project_onto_node(unit_of_work, workspace, resource, result)

            return EnrichmentOutcome(
                profile=updated_profile, version=version, relation_proposals=relation_proposals
            )

    def _require_cited_evidence_known(
        self, result: EnrichmentResult, extracted: ExtractedContent
    ) -> None:
        known_hashes = {evidence.content_hash for evidence in extracted.evidence}
        unknown_hashes = sorted(set(result.evidence_content_hashes) - known_hashes)
        if unknown_hashes:
            raise UncitedEnrichmentEvidenceError(
                f"enrichment result cites evidence hash(es) {unknown_hashes} not present in "
                "this resource's own extracted content evidence"
            )

    def _resolve_and_persist_relation(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace: Workspace,
        resource: Resource,
        extracted: ExtractedContent,
        version: ResourceEnrichmentProfileVersion,
        proposed: ProposedRelation,
    ) -> RelationProposal:
        """Resolve `proposed.candidate_label` to a real node by exact identifier match --
        never anything the classifier's own output can pick directly (see
        `application.enrichment_adapters.EnrichmentProvider`) -- then auto-apply or file it in
        the Review Inbox.

        Auto-apply requires *all* of (review finding S4-R02, round 2): a resolved match, that
        match's own identifier/URL being independently verifiable inside this resource's own
        extracted text (`_is_independently_grounded()` -- computed by this code from the
        resource's own evidence, never asserted by the classifier), the proposal's `relation_kind`
        in the configured auto-apply policy, and confidence clearing the configured threshold. A
        self-reported high confidence alone -- exactly what injected source text could try to
        manipulate a real classifier into emitting -- is never sufficient by itself, and neither
        is a classifier merely echoing back a value it was told was valid.
        """
        candidate = unit_of_work.resources.get_by_canonical_identifier(
            resource.workspace_id, proposed.candidate_label.strip()
        )
        resolved_target_node_id = (
            candidate.node_id
            if candidate is not None and candidate.node_id != resource.node_id
            else None
        )
        is_independently_grounded = candidate is not None and self._is_independently_grounded(
            extracted, candidate
        )
        should_auto_apply = (
            resolved_target_node_id is not None
            and is_independently_grounded
            and proposed.relation_kind in self._auto_apply_relation_kinds
            and proposed.confidence >= self._auto_apply_confidence_threshold
        )
        status = (
            RelationProposalStatus.AUTO_APPLIED
            if should_auto_apply
            else RelationProposalStatus.NEEDS_REVIEW
        )
        proposal = RelationProposal(
            workspace_id=resource.workspace_id,
            profile_version_id=version.id,
            source_node_id=resource.node_id,
            relation_kind=proposed.relation_kind,
            candidate_label=proposed.candidate_label,
            confidence=proposed.confidence,
            explanation=proposed.explanation,
            status=status,
            resolved_target_node_id=resolved_target_node_id,
        )
        unit_of_work.relation_proposals.save_without_commit(proposal)

        if should_auto_apply and resolved_target_node_id is not None:
            edge_type = workspace.edge_type_by_name(proposed.relation_kind.value)
            if edge_type is None:
                raise EnrichmentError(
                    f"workspace {workspace.id} has no edge type named "
                    f"{proposed.relation_kind.value!r}"
                )
            self._apply_edge_idempotently(
                unit_of_work,
                workspace_id=resource.workspace_id,
                edge_type_id=edge_type.id,
                source_node_id=resource.node_id,
                target_node_id=resolved_target_node_id,
            )

        return proposal

    def _is_independently_grounded(self, extracted: ExtractedContent, candidate: Resource) -> bool:
        """Whether `candidate`'s own identity is verifiable from `extracted`'s text (review
        finding S4-R02, round 2) -- computed entirely by this code from data the classifier does
        not control the meaning of, never from anything the classifier asserted about itself.

        A classifier handed a whitelist of valid values to echo back proves nothing beyond having
        read the prompt; requiring the *resolved target's own* canonical identifier or source URL
        to actually appear in the resource's own extracted title/abstract/body is independently
        checkable evidence that the source really does reference that target.
        """
        haystack = " ".join(
            text.lower()
            for text in (extracted.title, extracted.abstract, extracted.body_markdown)
            if text
        )
        if not haystack:
            return False
        identifiers = {candidate.canonical_identifier.lower()}
        if candidate.source_url:
            identifiers.add(candidate.source_url.lower())
        return any(identifier in haystack for identifier in identifiers)

    def _apply_edge_idempotently(
        self,
        unit_of_work: ResearchUnitOfWork,
        *,
        workspace_id: WorkspaceId,
        edge_type_id: EdgeTypeId,
        source_node_id: NodeId,
        target_node_id: NodeId,
    ) -> None:
        """Create the auto-applied edge only if an identical one does not already exist (review
        finding S4-R05): re-enriching the same resource with the same accepted relation must
        never duplicate the graph edge, even though its `RelationProposal` legitimately repeats
        once per profile version. Enforced atomically by
        `EdgeRepository.save_if_absent_without_commit()` (a database uniqueness constraint), not
        a non-atomic read-then-insert check, so it is safe even under a genuine concurrent race
        between two enrichment passes."""
        edge = Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )
        unit_of_work.edges.save_if_absent_without_commit(edge)

    def _project_onto_node(
        self,
        unit_of_work: ResearchUnitOfWork,
        workspace: Workspace,
        resource: Resource,
        result: EnrichmentResult,
    ) -> None:
        """Best-effort: only writes when the workspace's schema already declares the two
        projection fields (EP-2026-012 ST-04 selected approach). Never required for the
        enrichment pass itself to succeed."""
        node = unit_of_work.nodes.get(resource.node_id)
        if node is None:
            return
        node_type = workspace.node_type_by_id(node.node_type_id)
        if node_type is None:
            return

        summary_field = _field_by_name(node_type, _ENRICHMENT_SUMMARY_FIELD_NAME)
        confidence_field = _field_by_name(node_type, _ENRICHMENT_CONFIDENCE_FIELD_NAME)
        if summary_field is None and confidence_field is None:
            return

        updated_field_values = dict(node.field_values)
        if summary_field is not None:
            updated_field_values[summary_field.id] = result.payload.summary[
                :_PROJECTED_SUMMARY_MAX_LENGTH
            ]
        if confidence_field is not None:
            updated_field_values[confidence_field.id] = result.confidence

        updated_node = node.model_copy(
            update={"field_values": updated_field_values, "updated_at": datetime.now(UTC)}
        )
        updated_node.validate_against(node_type)
        unit_of_work.nodes.save_without_commit(updated_node)


def _field_by_name(node_type: NodeType, name: str) -> FieldDefinition | None:
    return next((field for field in node_type.field_definitions if field.name == name), None)
