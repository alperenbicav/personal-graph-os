"""Idempotently ensure the workflow's semantic node/edge types exist on an existing workspace.

`default_schema.py` already seeds every semantic type with its `system_key` set for a brand
new workspace, so this module only matters for a workspace created before ST-04 introduced
`system_key`. It never overwrites a name, color, field, or status the user has since edited:
a type is adopted by role only when its name still matches the original default and it has no
system_key yet; otherwise a brand new type is created. A genuine collision — a name-matched
type already claimed by a *different* role — is reported rather than silently remapped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from personal_graph_os.application.default_schema import (
    _decision_node_type,
    _implementation_node_type,
    _resource_node_type,
    _takeaway_node_type,
    _task_node_type,
    _workflow_edge_types,
)
from personal_graph_os.application.semantic_keys import (
    DECISION_NODE_TYPE_KEY,
    IMPLEMENTATION_NODE_TYPE_KEY,
    RESOURCE_NODE_TYPE_KEY,
    TAKEAWAY_NODE_TYPE_KEY,
    TASK_NODE_TYPE_KEY,
)
from personal_graph_os.domain.errors import SchemaEditConflictError
from personal_graph_os.domain.schema import EdgeType, NodeType, Workspace


@dataclass(frozen=True)
class _NodeTypeRole:
    system_key: str
    default_name: str
    build_default: Callable[[], NodeType]


@dataclass(frozen=True)
class _EdgeTypeRole:
    system_key: str
    default_name: str
    build_default: Callable[[], EdgeType]


def _node_type_roles() -> tuple[_NodeTypeRole, ...]:
    return (
        _NodeTypeRole(RESOURCE_NODE_TYPE_KEY, "Resource", _resource_node_type),
        _NodeTypeRole(TAKEAWAY_NODE_TYPE_KEY, "Takeaway", _takeaway_node_type),
        _NodeTypeRole(DECISION_NODE_TYPE_KEY, "Decision", _decision_node_type),
        _NodeTypeRole(TASK_NODE_TYPE_KEY, "Task", _task_node_type),
        _NodeTypeRole(IMPLEMENTATION_NODE_TYPE_KEY, "Implementation", _implementation_node_type),
    )


def _edge_type_roles() -> tuple[_EdgeTypeRole, ...]:
    """One role per `_workflow_edge_types()` entry, each rebuildable independently of the
    others (`build_default` closes over that one default `EdgeType`, not the whole tuple)."""
    return tuple(
        _EdgeTypeRole(edge_type.system_key, edge_type.name, lambda et=edge_type: et)
        for edge_type in _workflow_edge_types()
        if edge_type.system_key is not None
    )


def _ensure_node_type_role(
    node_types: tuple[NodeType, ...], role: _NodeTypeRole
) -> tuple[NodeType, ...]:
    if any(nt.system_key == role.system_key for nt in node_types):
        return node_types  # already ensured; never touched again

    matched_by_name = next((nt for nt in node_types if nt.name == role.default_name), None)
    if matched_by_name is None:
        return (*node_types, role.build_default())

    if matched_by_name.system_key is not None:
        raise SchemaEditConflictError(
            f"cannot assign semantic role '{role.system_key}' to node type "
            f"'{role.default_name}': it already carries role '{matched_by_name.system_key}'"
        )

    # Adopt in place: keep every user edit (name/icon/color/fields/statuses), add only the key.
    adopted = matched_by_name.model_copy(update={"system_key": role.system_key})
    return (*(nt for nt in node_types if nt.id != matched_by_name.id), adopted)


def _ensure_edge_type_role(
    edge_types: tuple[EdgeType, ...], role: _EdgeTypeRole
) -> tuple[EdgeType, ...]:
    if any(et.system_key == role.system_key for et in edge_types):
        return edge_types

    matched_by_name = next((et for et in edge_types if et.name == role.default_name), None)
    if matched_by_name is None:
        return (*edge_types, role.build_default())

    if matched_by_name.system_key is not None:
        raise SchemaEditConflictError(
            f"cannot assign semantic role '{role.system_key}' to edge type "
            f"'{role.default_name}': it already carries role '{matched_by_name.system_key}'"
        )

    adopted = matched_by_name.model_copy(update={"system_key": role.system_key})
    return (*(et for et in edge_types if et.id != matched_by_name.id), adopted)


def ensure_semantic_schema(workspace: Workspace) -> Workspace:
    """Return a copy of `workspace` guaranteed to have every workflow node/edge role.

    Idempotent: calling this again on an already-ensured workspace returns an equivalent
    workspace unchanged. Raises `SchemaEditConflictError` if a role's expected default name is
    already claimed by a *different* semantic role rather than silently remapping either one.
    """
    node_types = workspace.node_types
    for role in _node_type_roles():
        node_types = _ensure_node_type_role(node_types, role)

    edge_types = workspace.edge_types
    for role in _edge_type_roles():
        edge_types = _ensure_edge_type_role(edge_types, role)

    return workspace.model_copy(update={"node_types": node_types, "edge_types": edge_types})
