from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from personal_graph_os.application.resource_detail_service import (
    RELATION_TARGET_NODE,
    RELATION_TARGET_RESOURCE,
    RELATION_TARGET_WORK_ITEM,
    ResourceDetailService,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.domain.documents import (
    Document,
    DocumentKind,
    DocumentLink,
    DocumentLinkTargetType,
)
from personal_graph_os.domain.enrichment import (
    CitedEvidenceReference,
    PaperEnrichmentPayload,
    ResourceEnrichmentProfile,
    ResourceEnrichmentProfileVersion,
)
from personal_graph_os.domain.graph import Edge, Node
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.ingestion import IngestionJob, IngestionStage
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.domain.schema import EdgeType
from personal_graph_os.domain.work_items import WorkItem, WorkItemKind, WorkItemType
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteDocumentLinkRepository,
    SqliteDocumentRepository,
    SqliteEdgeRepository,
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqliteResourceEnrichmentProfileRepository,
    SqliteResourceEnrichmentProfileVersionRepository,
    SqliteResourceRepository,
    SqliteWorkItemRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _setup(
    sqlite_connection: sqlite3.Connection,
) -> tuple[ResourceDetailService, ResourceService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    resource_service = ResourceService(
        workspace_repository,
        resource_repository,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    detail_service = ResourceDetailService(
        workspace_repository,
        resource_repository,
        SqliteNodeRepository(sqlite_connection),
        SqliteEdgeRepository(sqlite_connection),
        SqliteDocumentLinkRepository(sqlite_connection),
        SqliteDocumentRepository(sqlite_connection),
        SqliteResourceEnrichmentProfileRepository(sqlite_connection),
        SqliteResourceEnrichmentProfileVersionRepository(sqlite_connection),
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteWorkItemRepository(sqlite_connection),
    )
    return detail_service, resource_service, workspace.id


def test_detail_reports_no_relations_documents_enrichment_or_provenance_by_default(
    sqlite_connection: sqlite3.Connection,
) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    resource, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )

    detail = detail_service.get_detail(resource.id)

    assert detail.resource.id == resource.id
    assert detail.node_title == "A paper"
    assert detail.enrichment is None
    assert detail.relations == ()
    assert detail.related_documents == ()
    assert detail.provenance is None


def test_detail_includes_current_enrichment_version(
    sqlite_connection: sqlite3.Connection,
) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    resource, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00002"
    )
    profile = ResourceEnrichmentProfile(
        workspace_id=workspace_id,
        canonical_identifier=resource.canonical_identifier,
        resource_kind=ResourceKind.PAPER,
    )
    version = ResourceEnrichmentProfileVersion(
        profile_id=profile.id,
        version_number=1,
        resource_kind=ResourceKind.PAPER,
        payload=PaperEnrichmentPayload(summary="a great paper"),
        evidence_content_hashes=("a" * 64,),
        cited_evidence=(
            CitedEvidenceReference(
                adapter_name="html_article_parser",
                source_reference="https://arxiv.org/abs/2401.00002",
                content_hash="a" * 64,
                retrieved_at=datetime(2026, 7, 25, tzinfo=UTC),
            ),
        ),
        authors=("Jane Doe", "John Smith"),
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        abstract="This paper studies things.",
        provider_name="fake",
        confidence=0.9,
        created_by="agent:test",
    )
    profile = profile.with_new_current_version(version)
    with SqliteResearchUnitOfWork(sqlite_connection) as unit_of_work:
        unit_of_work.resource_enrichment_profiles.save_without_commit(
            ResourceEnrichmentProfile(
                id=profile.id,
                workspace_id=workspace_id,
                canonical_identifier=resource.canonical_identifier,
                resource_kind=ResourceKind.PAPER,
            )
        )
        unit_of_work.resource_enrichment_profile_versions.create_version_without_commit(
            version, expected_current_version_number=0
        )
        unit_of_work.resource_enrichment_profiles.save_without_commit(profile)

    detail = detail_service.get_detail(resource.id)

    assert detail.enrichment is not None
    assert detail.enrichment.id == version.id
    assert isinstance(detail.enrichment.payload, PaperEnrichmentPayload)
    assert detail.enrichment.payload.summary == "a great paper"
    assert detail.enrichment.authors == ("Jane Doe", "John Smith")
    assert detail.enrichment.published_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert detail.enrichment.abstract == "This paper studies things."
    assert len(detail.enrichment.cited_evidence) == 1
    assert detail.enrichment.cited_evidence[0].content_hash == "a" * 64


def test_detail_includes_incident_graph_relations_in_both_directions(
    sqlite_connection: sqlite3.Connection,
) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    paper, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00003"
    )
    repo, _ = resource_service.create_or_reuse(
        workspace_id, "A repo", "https://github.com/acme/widgets"
    )
    workspace = SqliteWorkspaceRepository(sqlite_connection).get(workspace_id)
    assert workspace is not None
    edge_type = EdgeType(name="relates_to_custom")
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(
        workspace.model_copy(update={"edge_types": (*workspace.edge_types, edge_type)})
    )

    edge = Edge(
        workspace_id=workspace_id,
        edge_type_id=edge_type.id,
        source_node_id=paper.node_id,
        target_node_id=repo.node_id,
    )
    SqliteEdgeRepository(sqlite_connection).save(edge)

    paper_detail = detail_service.get_detail(paper.id)
    assert len(paper_detail.relations) == 1
    assert paper_detail.relations[0].direction == "outgoing"
    assert paper_detail.relations[0].node_id == repo.node_id
    assert paper_detail.relations[0].target_domain == RELATION_TARGET_RESOURCE
    assert paper_detail.relations[0].target_entity_id == repo.id
    assert paper_detail.relations[0].resource_kind == ResourceKind.GITHUB_REPOSITORY.value

    repo_detail = detail_service.get_detail(repo.id)
    assert len(repo_detail.relations) == 1
    assert repo_detail.relations[0].direction == "incoming"
    assert repo_detail.relations[0].node_id == paper.node_id
    assert repo_detail.relations[0].target_domain == RELATION_TARGET_RESOURCE
    assert repo_detail.relations[0].target_entity_id == paper.id


def test_detail_distinguishes_work_item_and_generic_node_relation_targets(
    sqlite_connection: sqlite3.Connection,
) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    paper, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00033"
    )
    workspace = SqliteWorkspaceRepository(sqlite_connection).get(workspace_id)
    assert workspace is not None
    edge_type = EdgeType(name="relates_to_custom")
    SqliteWorkspaceRepository(sqlite_connection).save(
        workspace.model_copy(update={"edge_types": (*workspace.edge_types, edge_type)})
    )
    work_item_node_type = workspace.node_types[0]

    work_item_node = Node(
        workspace_id=workspace_id, node_type_id=work_item_node_type.id, title="An epic"
    )
    SqliteNodeRepository(sqlite_connection).save(work_item_node)
    work_item = WorkItem(
        workspace_id=workspace_id,
        node_id=work_item_node.id,
        kind=WorkItemKind.EPIC,
        work_type=WorkItemType.FEATURE,
        source="manual",
    )
    SqliteWorkItemRepository(sqlite_connection).save(work_item)
    SqliteEdgeRepository(sqlite_connection).save(
        Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type.id,
            source_node_id=paper.node_id,
            target_node_id=work_item_node.id,
        )
    )

    generic_node = Node(
        workspace_id=workspace_id, node_type_id=work_item_node_type.id, title="A generic note"
    )
    SqliteNodeRepository(sqlite_connection).save(generic_node)
    SqliteEdgeRepository(sqlite_connection).save(
        Edge(
            workspace_id=workspace_id,
            edge_type_id=edge_type.id,
            source_node_id=paper.node_id,
            target_node_id=generic_node.id,
        )
    )

    detail = detail_service.get_detail(paper.id)
    relations_by_node = {relation.node_id: relation for relation in detail.relations}

    work_item_relation = relations_by_node[work_item_node.id]
    assert work_item_relation.target_domain == RELATION_TARGET_WORK_ITEM
    assert work_item_relation.target_entity_id == work_item.id
    assert work_item_relation.resource_kind is None

    generic_relation = relations_by_node[generic_node.id]
    assert generic_relation.target_domain == RELATION_TARGET_NODE
    assert generic_relation.target_entity_id is None
    assert generic_relation.resource_kind is None


def test_detail_includes_related_wiki_documents(sqlite_connection: sqlite3.Connection) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    resource, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00004"
    )
    document = Document(
        workspace_id=workspace_id, kind=DocumentKind.NOTE, title="My notes", source="manual"
    )
    SqliteDocumentRepository(sqlite_connection).save(document)
    link = DocumentLink(
        document_id=document.id,
        target_type=DocumentLinkTargetType.NODE,
        target_id=resource.node_id,
    )
    SqliteDocumentLinkRepository(sqlite_connection).save(link)

    detail = detail_service.get_detail(resource.id)

    assert len(detail.related_documents) == 1
    assert detail.related_documents[0].document_id == document.id
    assert detail.related_documents[0].title == "My notes"


def test_detail_includes_ingestion_job_provenance(sqlite_connection: sqlite3.Connection) -> None:
    detail_service, resource_service, workspace_id = _setup(sqlite_connection)
    resource, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00005"
    )
    job = IngestionJob(workspace_id=workspace_id, source="telegram", source_identifier="update:1")
    committed = (
        job.advance_to(IngestionStage.NORMALIZED)
        .advance_to(IngestionStage.EXTRACTED)
        .advance_to(IngestionStage.ENRICHED)
        .advance_to(IngestionStage.LINKED)
        .advance_to(
            IngestionStage.COMMITTED,
            result_entity_type="resource",
            result_entity_id=resource.id,
        )
    )
    SqliteIngestionJobRepository(sqlite_connection).save(committed)

    detail = detail_service.get_detail(resource.id)

    assert detail.provenance is not None
    assert detail.provenance.id == job.id
