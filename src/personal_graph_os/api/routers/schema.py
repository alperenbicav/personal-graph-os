"""Schema routes: creating/editing node types, field/status definitions, and edge types
all go through `SchemaService`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_schema_service
from personal_graph_os.api.schemas import (
    CreateEdgeTypeRequest,
    CreateFieldDefinitionRequest,
    CreateNodeTypeRequest,
    CreateStatusDefinitionRequest,
    UpdateEdgeTypeRequest,
    UpdateFieldDefinitionRequest,
    UpdateNodeTypeRequest,
    UpdateStatusDefinitionRequest,
)
from personal_graph_os.application.services import SchemaService
from personal_graph_os.domain.identifiers import (
    EdgeTypeId,
    FieldDefinitionId,
    NodeTypeId,
    StatusDefinitionId,
    WorkspaceId,
)
from personal_graph_os.domain.schema import EdgeType, FieldDefinition, NodeType, StatusDefinition

router = APIRouter(tags=["schema"])


@router.post("/node-types", response_model=NodeType, status_code=201)
def create_node_type(
    payload: CreateNodeTypeRequest, schema_service: SchemaService = Depends(get_schema_service)
) -> NodeType:
    return schema_service.create_node_type(
        WorkspaceId(payload.workspace_id),
        payload.name,
        icon=payload.icon,
        color_hex=payload.color_hex,
    )


@router.patch("/node-types/{node_type_id}", response_model=NodeType)
def update_node_type(
    node_type_id: str,
    payload: UpdateNodeTypeRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> NodeType:
    return schema_service.update_node_type(
        WorkspaceId(payload.workspace_id),
        NodeTypeId(node_type_id),
        name=payload.name,
        icon=payload.icon,
        color_hex=payload.color_hex,
    )


@router.post("/node-types/{node_type_id}/fields", response_model=FieldDefinition, status_code=201)
def add_field_definition(
    node_type_id: str,
    payload: CreateFieldDefinitionRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> FieldDefinition:
    return schema_service.add_field_definition(
        WorkspaceId(payload.workspace_id),
        NodeTypeId(node_type_id),
        payload.name,
        payload.field_type,
        is_required=payload.is_required,
        select_options=tuple(payload.select_options),
        description=payload.description,
    )


@router.patch(
    "/node-types/{node_type_id}/fields/{field_definition_id}", response_model=FieldDefinition
)
def update_field_definition(
    node_type_id: str,
    field_definition_id: str,
    payload: UpdateFieldDefinitionRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> FieldDefinition:
    return schema_service.update_field_definition(
        WorkspaceId(payload.workspace_id),
        NodeTypeId(node_type_id),
        FieldDefinitionId(field_definition_id),
        name=payload.name,
        field_type=payload.field_type,
        is_required=payload.is_required,
        select_options=tuple(payload.select_options)
        if payload.select_options is not None
        else None,
        description=payload.description,
        clear_description=payload.clear_description,
    )


@router.delete("/node-types/{node_type_id}/fields/{field_definition_id}", status_code=204)
def remove_field_definition(
    node_type_id: str,
    field_definition_id: str,
    workspace_id: str,
    schema_service: SchemaService = Depends(get_schema_service),
) -> None:
    schema_service.remove_field_definition(
        WorkspaceId(workspace_id), NodeTypeId(node_type_id), FieldDefinitionId(field_definition_id)
    )


@router.post(
    "/node-types/{node_type_id}/statuses", response_model=StatusDefinition, status_code=201
)
def add_status_definition(
    node_type_id: str,
    payload: CreateStatusDefinitionRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> StatusDefinition:
    return schema_service.add_status_definition(
        WorkspaceId(payload.workspace_id),
        NodeTypeId(node_type_id),
        payload.name,
        color_hex=payload.color_hex,
        is_terminal=payload.is_terminal,
        sort_order=payload.sort_order,
    )


@router.patch(
    "/node-types/{node_type_id}/statuses/{status_definition_id}", response_model=StatusDefinition
)
def update_status_definition(
    node_type_id: str,
    status_definition_id: str,
    payload: UpdateStatusDefinitionRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> StatusDefinition:
    return schema_service.update_status_definition(
        WorkspaceId(payload.workspace_id),
        NodeTypeId(node_type_id),
        StatusDefinitionId(status_definition_id),
        name=payload.name,
        color_hex=payload.color_hex,
        is_terminal=payload.is_terminal,
        sort_order=payload.sort_order,
    )


@router.delete("/node-types/{node_type_id}/statuses/{status_definition_id}", status_code=204)
def remove_status_definition(
    node_type_id: str,
    status_definition_id: str,
    workspace_id: str,
    schema_service: SchemaService = Depends(get_schema_service),
) -> None:
    schema_service.remove_status_definition(
        WorkspaceId(workspace_id),
        NodeTypeId(node_type_id),
        StatusDefinitionId(status_definition_id),
    )


@router.post("/edge-types", response_model=EdgeType, status_code=201)
def create_edge_type(
    payload: CreateEdgeTypeRequest, schema_service: SchemaService = Depends(get_schema_service)
) -> EdgeType:
    return schema_service.create_edge_type(
        WorkspaceId(payload.workspace_id),
        payload.name,
        inverse_name=payload.inverse_name,
        color_hex=payload.color_hex,
    )


@router.patch("/edge-types/{edge_type_id}", response_model=EdgeType)
def update_edge_type(
    edge_type_id: str,
    payload: UpdateEdgeTypeRequest,
    schema_service: SchemaService = Depends(get_schema_service),
) -> EdgeType:
    return schema_service.update_edge_type(
        WorkspaceId(payload.workspace_id),
        EdgeTypeId(edge_type_id),
        name=payload.name,
        inverse_name=payload.inverse_name,
        clear_inverse_name=payload.clear_inverse_name,
        color_hex=payload.color_hex,
    )


@router.delete("/edge-types/{edge_type_id}", status_code=204)
def remove_edge_type(
    edge_type_id: str,
    workspace_id: str,
    schema_service: SchemaService = Depends(get_schema_service),
) -> None:
    schema_service.remove_edge_type(WorkspaceId(workspace_id), EdgeTypeId(edge_type_id))
