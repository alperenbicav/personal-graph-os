"""Request DTOs for the HTTP API.

Domain models (`Node`, `Edge`, `Canvas`, `CanvasPlacement`, `Workspace`) are already typed
Pydantic models and are returned directly as response bodies. Requests need their own shape
because a capture/update/move is a partial, caller-supplied input, not a full entity.
"""

from __future__ import annotations

from pydantic import BaseModel

from personal_graph_os.domain.schema import FieldType


class CaptureNodeRequest(BaseModel):
    workspace_id: str
    node_type_id: str
    title: str


class UpdateNodeRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    status_id: str | None = None
    field_values: dict[str, object] | None = None


class ConnectEdgeRequest(BaseModel):
    workspace_id: str
    edge_type_id: str
    source_node_id: str
    target_node_id: str


class CreateCanvasRequest(BaseModel):
    workspace_id: str
    name: str


class PlaceNodeRequest(BaseModel):
    node_id: str
    position_x: float
    position_y: float


class UpdatePlacementRequest(BaseModel):
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = None
    height: float | None = None
    is_collapsed: bool | None = None


class CreateNodeTypeRequest(BaseModel):
    workspace_id: str
    name: str
    icon: str = "circle"
    color_hex: str = "#6b7280"


class UpdateNodeTypeRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    icon: str | None = None
    color_hex: str | None = None


class CreateFieldDefinitionRequest(BaseModel):
    workspace_id: str
    name: str
    field_type: FieldType
    is_required: bool = False
    select_options: list[str] = []
    description: str | None = None


class UpdateFieldDefinitionRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    field_type: FieldType | None = None
    is_required: bool | None = None
    select_options: list[str] | None = None
    description: str | None = None
    clear_description: bool = False


class CreateStatusDefinitionRequest(BaseModel):
    workspace_id: str
    name: str
    color_hex: str = "#6b7280"
    is_terminal: bool = False
    sort_order: int = 0


class UpdateStatusDefinitionRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    color_hex: str | None = None
    is_terminal: bool | None = None
    sort_order: int | None = None


class CreateEdgeTypeRequest(BaseModel):
    workspace_id: str
    name: str
    inverse_name: str | None = None
    color_hex: str = "#6b7280"


class UpdateEdgeTypeRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    inverse_name: str | None = None
    clear_inverse_name: bool = False
    color_hex: str | None = None
