"""Managed-attachment and non-copying file-reference routes, both through `FileService`.

`upload_attachment` is a plain (non-`async`) route — FastAPI runs it in its worker thread
pool — so `_iter_upload_chunks` can read `UploadFile.file` (the already-received, Starlette-
spooled body) synchronously in bounded chunks and hand them to the store as a lazy generator.
Nothing here accumulates the request body into its own `list`/`bytes` buffer: the configured
size limit is enforced by `ManagedFileStore.receive_upload` as it consumes each chunk, so an
oversize upload is rejected while streaming rather than after being fully buffered.
`download_attachment` never trusts the stored `file_name` as a header value verbatim: it goes
through `_content_disposition`, which mirrors Starlette's own quoting so a crafted name cannot
inject an extra header field.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from personal_graph_os.api.dependencies import get_file_service
from personal_graph_os.api.schemas import CreateFileReferenceRequest
from personal_graph_os.application.file_service import FileService
from personal_graph_os.domain.files import Attachment, FileReference
from personal_graph_os.domain.identifiers import AttachmentId, FileReferenceId, NodeId

router = APIRouter(tags=["files"])

_UPLOAD_READ_CHUNK_BYTES = 65536
_DOWNLOAD_CHUNK_BYTES = 65536


def _iter_upload_chunks(upload: UploadFile) -> Iterator[bytes]:
    upload.file.seek(0)
    while chunk := upload.file.read(_UPLOAD_READ_CHUNK_BYTES):
        yield chunk


def _content_disposition(file_name: str) -> str:
    encoded = quote(file_name)
    if encoded != file_name:
        return f"attachment; filename*=utf-8''{encoded}"
    return f'attachment; filename="{file_name}"'


@router.get("/nodes/{node_id}/attachments", response_model=list[Attachment])
def list_attachments(
    node_id: str, file_service: FileService = Depends(get_file_service)
) -> list[Attachment]:
    return list(file_service.list_attachments(NodeId(node_id)))


@router.post("/nodes/{node_id}/attachments", response_model=Attachment)
def upload_attachment(
    node_id: str,
    file: UploadFile,
    response: Response,
    file_service: FileService = Depends(get_file_service),
) -> Attachment:
    if not file.filename:
        raise HTTPException(status_code=422, detail="file must have a name")
    attachment = file_service.upload_attachment(
        NodeId(node_id),
        file_name=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        chunks=_iter_upload_chunks(file),
    )
    response.status_code = 201
    return attachment


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str, file_service: FileService = Depends(get_file_service)
) -> StreamingResponse:
    attachment, opened_file = file_service.download_attachment(AttachmentId(attachment_id))

    def _iter_bytes() -> Iterator[bytes]:
        with opened_file:
            while chunk := opened_file.read(_DOWNLOAD_CHUNK_BYTES):
                yield chunk

    return StreamingResponse(
        _iter_bytes(),
        media_type=attachment.mime_type,
        headers={
            "Content-Disposition": _content_disposition(attachment.file_name),
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(attachment.size_bytes),
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: str, file_service: FileService = Depends(get_file_service)
) -> None:
    file_service.delete_attachment(AttachmentId(attachment_id))


@router.get("/nodes/{node_id}/file-references", response_model=list[FileReference])
def list_file_references(
    node_id: str, file_service: FileService = Depends(get_file_service)
) -> list[FileReference]:
    return list(file_service.list_file_references(NodeId(node_id)))


@router.post("/nodes/{node_id}/file-references", response_model=FileReference)
def create_file_reference(
    node_id: str,
    payload: CreateFileReferenceRequest,
    response: Response,
    file_service: FileService = Depends(get_file_service),
) -> FileReference:
    response.status_code = 201
    return file_service.create_file_reference(
        NodeId(node_id),
        machine_name=payload.machine_name,
        relative_path=payload.relative_path,
        repository_name=payload.repository_name,
        absolute_path=payload.absolute_path,
        git_ref=payload.git_ref,
    )


@router.delete("/file-references/{file_reference_id}", status_code=204)
def delete_file_reference(
    file_reference_id: str, file_service: FileService = Depends(get_file_service)
) -> None:
    file_service.delete_file_reference(FileReferenceId(file_reference_id))


@router.post("/file-references/{file_reference_id}/verify", response_model=FileReference)
def verify_file_reference(
    file_reference_id: str, file_service: FileService = Depends(get_file_service)
) -> FileReference:
    return file_service.verify_file_reference(FileReferenceId(file_reference_id))
