"""Local workspace bootstrap: ensure a default workspace and canvas exist.

The MVP runs a single local workspace. Any caller that needs one (the HTTP API today, an
MCP server later) can reuse this instead of re-implementing "get or create the first one."
"""

from __future__ import annotations

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.repositories import CanvasRepository, WorkspaceRepository
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.canvas import Canvas
from personal_graph_os.domain.schema import Workspace

DEFAULT_WORKSPACE_NAME = "Personal"
DEFAULT_CANVAS_NAME = "Main"


def get_or_create_default_workspace(workspaces: WorkspaceRepository) -> Workspace:
    existing = workspaces.list_all()
    if existing:
        return existing[0]

    workspace = seed_default_schema(new_workspace(DEFAULT_WORKSPACE_NAME))
    workspaces.save(workspace)
    return workspace


def get_or_create_default_canvas(canvases: CanvasRepository, workspace: Workspace) -> Canvas:
    existing = canvases.list_by_workspace(workspace.id)
    if existing:
        return existing[0]

    canvas = Canvas(workspace_id=workspace.id, name=DEFAULT_CANVAS_NAME)
    canvases.save(canvas)
    return canvas
