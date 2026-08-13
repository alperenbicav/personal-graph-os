"""Wiki routes (EP-2026-012 ST-07): documents, their versions/links, and their
collection/tag taxonomy, all through `DocumentService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_document_service
from personal_graph_os.api.schemas import (
    AddDocumentLinkRequest,
    CreateCollectionRequest,
    CreateDocumentRequest,
    CreateTagRequest,
    DocumentDetailResponse,
    EditDocumentBodyRequest,
    UpdateDocumentMetadataRequest,
)
from personal_graph_os.application.document_service import DocumentService
from personal_graph_os.domain.documents import (
    Collection,
    Document,
    DocumentLink,
    DocumentVersion,
    Tag,
)
from personal_graph_os.domain.identifiers import (
    CollectionId,
    DocumentId,
    DocumentLinkId,
    WorkspaceId,
)

router = APIRouter(tags=["wiki"])


@router.get("/documents", response_model=list[Document])
def list_documents(
    workspace_id: str,
    collection_id: str | None = None,
    include_archived: bool = False,
    documents: DocumentService = Depends(get_document_service),
) -> list[Document]:
    return list(
        documents.list_documents(
            WorkspaceId(workspace_id),
            collection_id=CollectionId(collection_id) if collection_id is not None else None,
            include_archived=include_archived,
        )
    )


@router.get("/documents/{document_id}", response_model=Document)
def get_document(
    document_id: str, documents: DocumentService = Depends(get_document_service)
) -> Document:
    return documents.get(DocumentId(document_id))


@router.get("/documents/{document_id}/detail", response_model=DocumentDetailResponse)
def get_document_detail(
    document_id: str, documents: DocumentService = Depends(get_document_service)
) -> DocumentDetailResponse:
    return DocumentDetailResponse.from_detail(documents.get_detail(DocumentId(document_id)))


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersion])
def list_document_versions(
    document_id: str, documents: DocumentService = Depends(get_document_service)
) -> list[DocumentVersion]:
    return list(documents.list_versions(DocumentId(document_id)))


@router.get("/documents/{document_id}/backlinks", response_model=list[Document])
def list_document_backlinks(
    document_id: str, documents: DocumentService = Depends(get_document_service)
) -> list[Document]:
    return list(documents.list_backlinks(DocumentId(document_id)))


@router.post("/documents", response_model=Document)
def create_document(
    payload: CreateDocumentRequest, documents: DocumentService = Depends(get_document_service)
) -> Document:
    document, _ = documents.create_document(
        WorkspaceId(payload.workspace_id),
        title=payload.title,
        kind=payload.kind,
        source=payload.source,
        body_markdown=payload.body_markdown,
        collection_id=(
            CollectionId(payload.collection_id) if payload.collection_id is not None else None
        ),
        tag_names=tuple(payload.tag_names),
    )
    return document


@router.patch("/documents/{document_id}", response_model=Document)
def update_document_metadata(
    document_id: str,
    payload: UpdateDocumentMetadataRequest,
    documents: DocumentService = Depends(get_document_service),
) -> Document:
    return documents.update_metadata(
        DocumentId(document_id),
        title=payload.title,
        kind=payload.kind,
        collection_id=(
            CollectionId(payload.collection_id) if payload.collection_id is not None else None
        ),
        clear_collection=payload.clear_collection,
        tag_names=tuple(payload.tag_names) if payload.tag_names is not None else None,
        is_archived=payload.is_archived,
    )


@router.post("/documents/{document_id}/versions", response_model=DocumentVersion)
def edit_document_body(
    document_id: str,
    payload: EditDocumentBodyRequest,
    documents: DocumentService = Depends(get_document_service),
) -> DocumentVersion:
    return documents.edit_body(
        DocumentId(document_id), body_markdown=payload.body_markdown, actor=payload.actor
    )


@router.post("/documents/{document_id}/links", response_model=DocumentLink)
def add_document_link(
    document_id: str,
    payload: AddDocumentLinkRequest,
    documents: DocumentService = Depends(get_document_service),
) -> DocumentLink:
    return documents.add_link(DocumentId(document_id), payload.target_type, payload.target_id)


@router.delete("/document-links/{document_link_id}", status_code=204)
def remove_document_link(
    document_link_id: str, documents: DocumentService = Depends(get_document_service)
) -> None:
    documents.remove_link(DocumentLinkId(document_link_id))


@router.get("/collections", response_model=list[Collection])
def list_collections(
    workspace_id: str, documents: DocumentService = Depends(get_document_service)
) -> list[Collection]:
    return list(documents.list_collections(WorkspaceId(workspace_id)))


@router.post("/collections", response_model=Collection)
def create_collection(
    payload: CreateCollectionRequest, documents: DocumentService = Depends(get_document_service)
) -> Collection:
    return documents.create_collection(
        WorkspaceId(payload.workspace_id),
        payload.name,
        parent_id=CollectionId(payload.parent_id) if payload.parent_id is not None else None,
    )


@router.get("/tags", response_model=list[Tag])
def list_tags(
    workspace_id: str, documents: DocumentService = Depends(get_document_service)
) -> list[Tag]:
    return list(documents.list_tags(WorkspaceId(workspace_id)))


@router.post("/tags", response_model=Tag)
def create_tag(
    payload: CreateTagRequest, documents: DocumentService = Depends(get_document_service)
) -> Tag:
    return documents.get_or_create_tag(WorkspaceId(payload.workspace_id), payload.name)
