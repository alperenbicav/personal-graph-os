"""User-editable schema: node types, field definitions, statuses, and edge types.

Schema is user data, not code. The MVP field-type set is fixed (CQ-02: text, number,
boolean, date, select, object reference, URL, and file/path); a plugin mechanism for
arbitrary field types is deferred past the MVP.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from personal_graph_os.domain.errors import FieldValueTypeError, InvariantViolationError
from personal_graph_os.domain.identifiers import (
    EdgeTypeId,
    FieldDefinitionId,
    NodeTypeId,
    StatusDefinitionId,
    WorkspaceId,
    new_id,
)

_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


class FieldType(StrEnum):
    """The MVP's fixed set of field-value types (CQ-02 default)."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    SELECT = "select"
    OBJECT_REFERENCE = "object_reference"
    URL = "url"
    FILE_PATH = "file_path"


def _non_empty(value: str, field_label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvariantViolationError(f"{field_label} must not be empty")
    return stripped


class FieldDefinition(BaseModel):
    """A user-defined field that a `NodeType` exposes on its nodes."""

    id: FieldDefinitionId = Field(default_factory=lambda: FieldDefinitionId(new_id()))
    name: str
    field_type: FieldType
    # "Required once set", not "always present": product principle 6 (capture requires only
    # a title) means a node may never supply this field's value and stay valid indefinitely.
    # This flag only rejects an *explicit* empty/incompatible value for the field once a
    # caller does try to set one — see `validate_value()` and `Node.validate_against()`,
    # which only checks field ids actually present in `field_values`.
    is_required: bool = False
    select_options: tuple[str, ...] = ()
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "FieldDefinition.name")

    @model_validator(mode="after")
    def _validate_select_type_declares_options(self) -> FieldDefinition:
        if self.field_type is FieldType.SELECT and not self.select_options:
            raise InvariantViolationError(
                "FieldDefinition with field_type=select must declare select_options"
            )
        return self

    def validate_value(self, value: object) -> None:
        """Raise `FieldValueTypeError` unless `value` matches this field's declared type."""
        if value is None:
            if self.is_required:
                raise FieldValueTypeError(f"field '{self.name}' cannot be cleared to empty")
            return

        checks: dict[FieldType, tuple[type | tuple[type, ...], str]] = {
            FieldType.TEXT: (str, "a string"),
            FieldType.NUMBER: ((int, float), "a number"),
            FieldType.BOOLEAN: (bool, "a boolean"),
            FieldType.FILE_PATH: (str, "a file path string"),
        }
        if self.field_type in checks:
            expected_type, description = checks[self.field_type]
            is_bool_mismatch = expected_type is not bool and isinstance(value, bool)
            if is_bool_mismatch or not isinstance(value, expected_type):
                raise FieldValueTypeError(
                    f"field '{self.name}' expects {description}, got {type(value).__name__}"
                )
            return

        if self.field_type is FieldType.DATE:
            # Field values are persisted as JSON, so the one canonical representation for a
            # date is an ISO-8601 string; `date`/`datetime` objects are not JSON-serializable
            # and are rejected here rather than accepted and failing later at the storage
            # boundary.
            if not isinstance(value, str):
                raise FieldValueTypeError(
                    f"field '{self.name}' expects an ISO-8601 date string, "
                    f"got {type(value).__name__}"
                )
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise FieldValueTypeError(
                    f"field '{self.name}' expects an ISO-8601 date string"
                ) from error

        if self.field_type is FieldType.SELECT:
            if value not in self.select_options:
                raise FieldValueTypeError(
                    f"field '{self.name}' expects one of {self.select_options}, got {value!r}"
                )
            return

        if self.field_type is FieldType.OBJECT_REFERENCE:
            if not isinstance(value, str) or not value:
                raise FieldValueTypeError(
                    f"field '{self.name}' expects a non-empty node id reference"
                )
            return

        if self.field_type is FieldType.URL:
            if not isinstance(value, str):
                raise FieldValueTypeError(
                    f"field '{self.name}' expects a URL string, got {type(value).__name__}"
                )
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise FieldValueTypeError(
                    f"field '{self.name}' expects an absolute http:// or https:// URL"
                )
            return


class StatusDefinition(BaseModel):
    """A user-defined status a `Node` of a given `NodeType` may hold."""

    id: StatusDefinitionId = Field(default_factory=lambda: StatusDefinitionId(new_id()))
    name: str
    color_hex: str = "#6b7280"
    is_terminal: bool = False
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "StatusDefinition.name")

    @field_validator("color_hex")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        if not _HEX_COLOR_PATTERN.match(value):
            raise InvariantViolationError(f"StatusDefinition.color_hex is not a hex color: {value}")
        return value


class EdgeType(BaseModel):
    """A user-defined, typed, directional relationship between two nodes."""

    id: EdgeTypeId = Field(default_factory=lambda: EdgeTypeId(new_id()))
    name: str
    inverse_name: str | None = None
    color_hex: str = "#6b7280"

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "EdgeType.name")

    @field_validator("color_hex")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        if not _HEX_COLOR_PATTERN.match(value):
            raise InvariantViolationError(f"EdgeType.color_hex is not a hex color: {value}")
        return value


class NodeType(BaseModel):
    """A user-defined kind of node, with its own fields, statuses, icon, and color."""

    id: NodeTypeId = Field(default_factory=lambda: NodeTypeId(new_id()))
    name: str
    icon: str = "circle"
    color_hex: str = "#6b7280"
    field_definitions: tuple[FieldDefinition, ...] = ()
    status_definitions: tuple[StatusDefinition, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "NodeType.name")

    @field_validator("color_hex")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        if not _HEX_COLOR_PATTERN.match(value):
            raise InvariantViolationError(f"NodeType.color_hex is not a hex color: {value}")
        return value

    @field_validator("field_definitions")
    @classmethod
    def _validate_unique_field_names(
        cls, value: tuple[FieldDefinition, ...]
    ) -> tuple[FieldDefinition, ...]:
        names = [definition.name for definition in value]
        if len(names) != len(set(names)):
            raise InvariantViolationError("NodeType field_definitions must have unique names")
        return value

    @field_validator("status_definitions")
    @classmethod
    def _validate_unique_status_names(
        cls, value: tuple[StatusDefinition, ...]
    ) -> tuple[StatusDefinition, ...]:
        names = [definition.name for definition in value]
        if len(names) != len(set(names)):
            raise InvariantViolationError("NodeType status_definitions must have unique names")
        return value

    def field_by_id(self, field_definition_id: FieldDefinitionId) -> FieldDefinition | None:
        return next((f for f in self.field_definitions if f.id == field_definition_id), None)

    def status_by_id(self, status_definition_id: StatusDefinitionId) -> StatusDefinition | None:
        return next((s for s in self.status_definitions if s.id == status_definition_id), None)


class Workspace(BaseModel):
    """A local workspace: the schema container and unit of export/backup.

    CQ-01 default: a workspace holds multiple canvases plus a global graph/search view
    over every node it owns; it is not a single infinite canvas.
    """

    id: WorkspaceId = Field(default_factory=lambda: WorkspaceId(new_id()))
    name: str
    node_types: tuple[NodeType, ...] = ()
    edge_types: tuple[EdgeType, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _non_empty(value, "Workspace.name")

    @field_validator("node_types")
    @classmethod
    def _validate_unique_node_type_names(cls, value: tuple[NodeType, ...]) -> tuple[NodeType, ...]:
        names = [node_type.name for node_type in value]
        if len(names) != len(set(names)):
            raise InvariantViolationError("Workspace node_types must have unique names")
        return value

    @field_validator("edge_types")
    @classmethod
    def _validate_unique_edge_type_names(cls, value: tuple[EdgeType, ...]) -> tuple[EdgeType, ...]:
        names = [edge_type.name for edge_type in value]
        if len(names) != len(set(names)):
            raise InvariantViolationError("Workspace edge_types must have unique names")
        return value

    def node_type_by_id(self, node_type_id: NodeTypeId) -> NodeType | None:
        return next((nt for nt in self.node_types if nt.id == node_type_id), None)

    def edge_type_by_id(self, edge_type_id: EdgeTypeId) -> EdgeType | None:
        return next((et for et in self.edge_types if et.id == edge_type_id), None)
