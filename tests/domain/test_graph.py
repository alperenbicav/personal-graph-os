from __future__ import annotations

import pytest

from personal_graph_os.domain.errors import (
    FieldValueTypeError,
    InvariantViolationError,
    UnknownSchemaReferenceError,
)
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import StatusDefinitionId, WorkspaceId, new_id
from personal_graph_os.domain.schema import (
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)


def _task_workspace_and_type() -> tuple[Workspace, NodeType]:
    task_type = NodeType(
        name="Task",
        field_definitions=(
            FieldDefinition(
                name="priority", field_type=FieldType.SELECT, select_options=("low", "high")
            ),
        ),
        status_definitions=(StatusDefinition(name="todo"), StatusDefinition(name="done")),
    )
    workspace = Workspace(name="Personal", node_types=(task_type,))
    return workspace, task_type


def _task_type_with_required_field() -> NodeType:
    return NodeType(
        name="Task",
        field_definitions=(
            FieldDefinition(name="owner", field_type=FieldType.TEXT, is_required=True),
        ),
    )


def test_node_capture_requires_only_a_title() -> None:
    workspace, task_type = _task_workspace_and_type()
    node = Node(workspace_id=workspace.id, node_type_id=task_type.id, title="Write report")
    node.validate_against(task_type)


def test_node_validate_against_rejects_unknown_status() -> None:
    workspace, task_type = _task_workspace_and_type()
    node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="Write report",
        status_id=StatusDefinitionId(new_id()),
    )
    with pytest.raises(UnknownSchemaReferenceError):
        node.validate_against(task_type)


def test_node_validate_against_rejects_unknown_field() -> None:
    workspace, task_type = _task_workspace_and_type()
    node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="Write report",
        field_values={new_id(): "value"},
    )
    with pytest.raises(UnknownSchemaReferenceError):
        node.validate_against(task_type)


def test_node_validate_against_rejects_mismatched_field_value() -> None:
    workspace, task_type = _task_workspace_and_type()
    priority_field = task_type.field_definitions[0]
    node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="Write report",
        field_values={priority_field.id: "medium"},
    )
    with pytest.raises(FieldValueTypeError):
        node.validate_against(task_type)


def test_node_title_cannot_be_blank() -> None:
    workspace, task_type = _task_workspace_and_type()
    with pytest.raises(InvariantViolationError):
        Node(workspace_id=workspace.id, node_type_id=task_type.id, title="   ")


def test_node_capture_stays_valid_forever_even_with_an_unset_required_field() -> None:
    """Regression: a required field must not retroactively invalidate a node that simply
    never set it. Product principle 6 ("capture requires only a title... without blocking
    the inbox flow") means this must hold not only at capture but for every later
    `validate_against()` call too, not just the moment the node was created."""
    task_type = _task_type_with_required_field()
    node = Node(workspace_id=WorkspaceId(new_id()), node_type_id=task_type.id, title="Write report")
    node.validate_against(task_type)


def test_node_rejects_an_explicit_none_for_a_required_field() -> None:
    task_type = _task_type_with_required_field()
    owner_field = task_type.field_definitions[0]
    node = Node(
        workspace_id=WorkspaceId(new_id()),
        node_type_id=task_type.id,
        title="Write report",
        field_values={owner_field.id: None},
    )
    with pytest.raises(FieldValueTypeError):
        node.validate_against(task_type)
