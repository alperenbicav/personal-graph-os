"""The canonical graph: `Node` and `Edge`.

Object identity and data live here. Spatial presentation (`Canvas`/`CanvasPlacement`) and
projections (`SavedView`) never hold canonical state of their own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError, UnknownSchemaReferenceError
from personal_graph_os.domain.identifiers import (
    EdgeId,
    EdgeTypeId,
    NodeId,
    NodeTypeId,
    StatusDefinitionId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.schema import FieldDefinition, NodeType


class Node(BaseModel):
    """A single typed object. Capture requires only a title (progressive complexity)."""

    id: NodeId = Field(default_factory=lambda: NodeId(new_id()))
    workspace_id: WorkspaceId
    node_type_id: NodeTypeId
    title: str
    body: str = ""
    status_id: StatusDefinitionId | None = None
    field_values: dict[str, Any] = Field(default_factory=dict)
    is_archived: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("Node.title must not be empty")
        return stripped

    def validate_against(self, node_type: NodeType) -> None:
        """Validate this node's status and field values against its declared `NodeType`.

        Raises `UnknownSchemaReferenceError` for a status/field id the type does not
        declare, and `FieldValueTypeError` (via `FieldDefinition.validate_value`) for a
        value that does not match its field's declared type.

        Only field ids actually present in `field_values` are checked against their
        definition — including a `required` one, so explicitly clearing a required field to
        `None` is still rejected. A field a node has simply never set is not synthesized into
        a `None` check here: quick capture creates a node with `field_values={}`, and product
        principle 6 ("capture requires only a title... without blocking the inbox flow")
        means a node must stay valid indefinitely without ever supplying a required field's
        value, not just at the moment it is captured.
        """
        if node_type.id != self.node_type_id:
            raise UnknownSchemaReferenceError(
                f"node {self.id} declares node_type_id {self.node_type_id}, "
                f"but was validated against node type {node_type.id}"
            )

        if self.status_id is not None and node_type.status_by_id(self.status_id) is None:
            raise UnknownSchemaReferenceError(
                f"node type '{node_type.name}' has no status {self.status_id}"
            )

        fields_by_id: dict[str, FieldDefinition] = {
            field.id: field for field in node_type.field_definitions
        }
        for field_definition_id, value in self.field_values.items():
            field_definition = fields_by_id.get(field_definition_id)
            if field_definition is None:
                raise UnknownSchemaReferenceError(
                    f"node type '{node_type.name}' has no field {field_definition_id}"
                )
            field_definition.validate_value(value)


class Edge(BaseModel):
    """A typed, directional relationship between two nodes."""

    id: EdgeId = Field(default_factory=lambda: EdgeId(new_id()))
    workspace_id: WorkspaceId
    edge_type_id: EdgeTypeId
    source_node_id: NodeId
    target_node_id: NodeId
    field_values: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
