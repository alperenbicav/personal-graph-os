from __future__ import annotations

from datetime import date

import pytest

from personal_graph_os.domain.errors import FieldValueTypeError, InvariantViolationError
from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)


def test_field_definition_rejects_select_type_without_options() -> None:
    with pytest.raises(InvariantViolationError):
        FieldDefinition(name="priority", field_type=FieldType.SELECT)


def test_field_definition_validate_value_accepts_matching_type() -> None:
    field = FieldDefinition(name="due_date", field_type=FieldType.DATE)
    field.validate_value("2026-08-01")


def test_field_definition_validate_value_rejects_python_date_object() -> None:
    """`date`/`datetime` objects are not JSON-serializable; the canonical DATE
    representation is an ISO-8601 string, so a raw `date` must be rejected here rather
    than accepted and fail later when the service tries to persist it as JSON."""
    field = FieldDefinition(name="due_date", field_type=FieldType.DATE)
    with pytest.raises(FieldValueTypeError):
        field.validate_value(date(2026, 8, 1))


def test_field_definition_validate_value_rejects_non_iso_date_string() -> None:
    field = FieldDefinition(name="due_date", field_type=FieldType.DATE)
    with pytest.raises(FieldValueTypeError):
        field.validate_value("08/01/2026")


def test_field_definition_validate_value_rejects_mismatched_type() -> None:
    field = FieldDefinition(
        name="priority", field_type=FieldType.SELECT, select_options=("low", "high")
    )
    with pytest.raises(FieldValueTypeError):
        field.validate_value("medium")


def test_field_definition_validate_value_rejects_bool_for_number() -> None:
    field = FieldDefinition(name="count", field_type=FieldType.NUMBER)
    with pytest.raises(FieldValueTypeError):
        field.validate_value(True)


def test_field_definition_required_rejects_missing_value() -> None:
    field = FieldDefinition(name="title", field_type=FieldType.TEXT, is_required=True)
    with pytest.raises(FieldValueTypeError):
        field.validate_value(None)


def test_field_definition_url_accepts_absolute_http_and_https() -> None:
    field = FieldDefinition(name="source_url", field_type=FieldType.URL)
    field.validate_value("https://example.com/paper")
    field.validate_value("http://example.com/paper")


@pytest.mark.parametrize(
    "value",
    ["definitely not a URL", "example.com", "ftp://example.com", "javascript:alert(1)", ""],
)
def test_field_definition_url_rejects_non_absolute_http_values(value: str) -> None:
    field = FieldDefinition(name="source_url", field_type=FieldType.URL)
    with pytest.raises(FieldValueTypeError):
        field.validate_value(value)


def test_node_type_rejects_duplicate_field_names() -> None:
    with pytest.raises(InvariantViolationError):
        NodeType(
            name="Task",
            field_definitions=(
                FieldDefinition(name="priority", field_type=FieldType.TEXT),
                FieldDefinition(name="priority", field_type=FieldType.NUMBER),
            ),
        )


def test_node_type_rejects_duplicate_status_names() -> None:
    with pytest.raises(InvariantViolationError):
        NodeType(
            name="Task",
            status_definitions=(
                StatusDefinition(name="open"),
                StatusDefinition(name="open"),
            ),
        )


def test_workspace_rejects_duplicate_node_type_names() -> None:
    with pytest.raises(InvariantViolationError):
        Workspace(name="Personal", node_types=(NodeType(name="Task"), NodeType(name="Task")))


def test_workspace_node_type_lookup_round_trips() -> None:
    task_type = NodeType(name="Task")
    workspace = Workspace(name="Personal", node_types=(task_type,))
    assert workspace.node_type_by_id(task_type.id) is task_type


def test_workspace_node_type_by_name_and_system_key_round_trip() -> None:
    task_type = NodeType(name="Task", system_key="task")
    workspace = Workspace(name="Personal", node_types=(task_type,))
    assert workspace.node_type_by_name("Task") is task_type
    assert workspace.node_type_by_system_key("task") is task_type
    assert workspace.node_type_by_name("Missing") is None
    assert workspace.node_type_by_system_key("missing") is None


def test_workspace_edge_type_by_name_and_system_key_round_trip() -> None:
    edge_type = EdgeType(name="derives", system_key="resource_yields_takeaway")
    workspace = Workspace(name="Personal", edge_types=(edge_type,))
    assert workspace.edge_type_by_name("derives") is edge_type
    assert workspace.edge_type_by_system_key("resource_yields_takeaway") is edge_type


def test_workspace_rejects_duplicate_node_type_system_keys() -> None:
    with pytest.raises(InvariantViolationError):
        Workspace(
            name="Personal",
            node_types=(
                NodeType(name="Task", system_key="task"),
                NodeType(name="Ticket", system_key="task"),
            ),
        )


def test_workspace_allows_multiple_node_types_with_no_system_key() -> None:
    workspace = Workspace(
        name="Personal", node_types=(NodeType(name="Task"), NodeType(name="Note"))
    )
    assert len(workspace.node_types) == 2


def test_workspace_rejects_duplicate_edge_type_system_keys() -> None:
    with pytest.raises(InvariantViolationError):
        Workspace(
            name="Personal",
            edge_types=(
                EdgeType(name="a", system_key="resource_yields_takeaway"),
                EdgeType(name="b", system_key="resource_yields_takeaway"),
            ),
        )
