"""Portable workspace export: a single authenticated download of a deterministic
`.pgos-export.zip` (ST-07.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from personal_graph_os.api.dependencies import get_export_service
from personal_graph_os.application.export_service import ExportService
from personal_graph_os.domain.identifiers import WorkspaceId

router = APIRouter(tags=["export"])


@router.get("/export")
def export_workspace(
    workspace_id: str, export_service: ExportService = Depends(get_export_service)
) -> FileResponse:
    zip_path = export_service.export_workspace(WorkspaceId(workspace_id))
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{workspace_id}.pgos-export.zip",
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
