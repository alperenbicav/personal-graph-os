"""Workspace bootstrap route: the frontend's entry point for schema and identifiers."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_default_canvas_id, get_workspace_repository
from personal_graph_os.application.repositories import WorkspaceRepository
from personal_graph_os.application.services import WorkspaceNotFoundError
from personal_graph_os.domain.identifiers import CanvasId
from personal_graph_os.domain.schema import Workspace

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("", response_model=Workspace)
def get_default_workspace(
    workspaces: WorkspaceRepository = Depends(get_workspace_repository),
) -> Workspace:
    workspace_list = workspaces.list_all()
    if not workspace_list:
        raise WorkspaceNotFoundError("no workspace has been bootstrapped")
    return workspace_list[0]


@router.get("/default-canvas-id", response_model=str)
def get_default_canvas(default_canvas_id: CanvasId = Depends(get_default_canvas_id)) -> str:
    return str(default_canvas_id)
